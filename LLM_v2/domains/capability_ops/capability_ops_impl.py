# ============================================================
# LLM_v2/domains/capability_ops/capability_ops_impl.py
# ============================================================
# 自主能力的准入 / 熔断实现（全部封装在能力域内部，不改核心架构）
#
# 借鉴来源：
#   - MUSE-Autoskill：技能生命周期（创建→登记→评估→迭代/弃用）
#   - OpenClaw Superpowers：静态扫描 + 沙箱执行 + 观察期 + 自动弃用
#   - ExpeL / AWM：成功轨迹可以作为后续能力生成的素材（P1）
#
# 依赖的既有设施（均无需修改）：
#   - LLM_v2/domains 自动发现与 build_<id>_domain 工厂
#   - DomainAssembly.rediscover() / start_domain() / stop_domain()
#   - EventBus：TASK_DONE、DOMAIN_TOOL_CALLED
# ============================================================

from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOMAINS_ROOT = _PROJECT_ROOT / "LLM_v2" / "domains"
REGISTRY_PATH = _PROJECT_ROOT / "persona" / "capabilities.json"
SNAPSHOT_ROOT = _PROJECT_ROOT / "persona" / "capability_snapshots"
QUARANTINE_ROOT = _PROJECT_ROOT / "persona" / "capability_quarantine"

SELF_ID = "capability_ops"

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
# AI 自主决策上限：R0-R2 由系统自行判定并挂载；R3 必须人工确认
AUTO_RISK_DEFAULT = "R2"
BUDGET_CALLS_DEFAULT = 500

# 各闸门超时（秒）——短超时保证"卡住"能快速暴露，而不是静默 60s
G2_TIMEOUT = float(os.getenv("CAPABILITY_G2_TIMEOUT") or 25)
G3_TIMEOUT = float(os.getenv("CAPABILITY_G3_TIMEOUT") or 40)

MAX_CONSECUTIVE_FAILURES = 3
MIN_CALLS_FOR_RATE = 5
MIN_SUCCESS_RATE = 0.8

# P3 技能演化：derived → captured → (fixed)，并带漂移检测
CAPTURE_MIN_CALLS = 10
CAPTURE_MIN_RATE = 0.8
DRIFT_WINDOW = 10
DRIFT_MIN_RATE = 0.6

_NETWORK_MODULES = {
    "requests", "httpx", "aiohttp", "urllib", "urllib3", "socket",
    "websockets", "websocket", "http", "ftplib", "smtplib", "openai",
    "dashscope", "bilibili_api", "mcp",
}
_SECRET_MODULES = {"keyring", "dotenv"}

_WRITE_CALLS = {
    "os": {"remove", "unlink", "rmdir", "removedirs", "rename", "renames",
           "replace", "mkdir", "makedirs", "chmod", "chown", "truncate"},
    "shutil": {"copy", "copy2", "copyfile", "copytree", "move", "rmtree",
               "make_archive", "unpack_archive"},
    "pathlib": {"write_text", "write_bytes", "touch", "unlink", "mkdir",
                "rename", "replace", "rmdir"},
}
_SHELL_CALLS = {"os.system", "os.popen", "subprocess.run", "subprocess.call",
                "subprocess.check_call", "subprocess.check_output",
                "subprocess.Popen", "subprocess.getoutput"}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}

_registry_lock = threading.RLock()
# mtime 缓存：DOMAIN_TOOL_CALLED 每次工具调用都会读记录，避免重复解析文件
_registry_cache: Dict[str, Any] = {"mtime": None, "data": None}


# ---------- 记录读写 ----------

def _load_registry() -> Dict[str, Any]:
    try:
        mtime = REGISTRY_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return {"version": 1, "capabilities": {}}
    if _registry_cache["mtime"] == mtime and _registry_cache["data"] is not None:
        return _registry_cache["data"]
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("capabilities"), dict):
            _registry_cache.update(mtime=mtime, data=data)
            return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 注册表读取失败（按空表处理）: {e}")
    return {"version": 1, "capabilities": {}}


def _save_registry(data: Dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)
    try:
        _registry_cache.update(mtime=REGISTRY_PATH.stat().st_mtime_ns, data=data)
    except OSError:
        _registry_cache.update(mtime=None, data=None)


def _get_record(capability_id: str) -> Optional[Dict[str, Any]]:
    with _registry_lock:
        return _load_registry()["capabilities"].get(capability_id)


def _upsert_record(record: Dict[str, Any]) -> None:
    with _registry_lock:
        data = _load_registry()
        data["capabilities"][record["id"]] = record
        _save_registry(data)


def _update_record(capability_id: str, **fields) -> Optional[Dict[str, Any]]:
    with _registry_lock:
        data = _load_registry()
        rec = data["capabilities"].get(capability_id)
        if rec is None:
            return None
        rec.update(fields)
        _save_registry(data)
        return rec


def forget_capability(capability_id: str) -> bool:
    """彻底移除一个能力域记录（自进化卸载时用，避免重启被当成待审能力）。"""
    with _registry_lock:
        data = _load_registry()
        existed = data["capabilities"].pop(capability_id, None) is not None
        if existed:
            _save_registry(data)
        return existed


def _runtime():
    from LLM_v2.core.persona_runtime import get_runtime
    return get_runtime()


def _log(level: str, message: str) -> None:
    """同时写主日志与 WebUI 运行日志（WebUI「日志」页可实时看到）"""
    if level == "WARN":
        logger.warning(f"[capability_ops] {message}")
    else:
        logger.info(f"[capability_ops] {message}")
    try:
        from LLM_v2.io.webui import log_event
        log_event(level, "capability", message)
    except Exception:  # noqa: BLE001
        pass


def _risk_limit() -> str:
    return (os.getenv("CAPABILITY_AUTO_RISK") or AUTO_RISK_DEFAULT).upper()


def _auto_admit_enabled() -> bool:
    return (os.getenv("CAPABILITY_AUTO_ADMIT") or "1") not in ("0", "false", "False")


# ---------- G1：静态扫描 ----------

def _attr_parts(node: ast.AST) -> List[str]:
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    if isinstance(cur, ast.Call):
        inner = _attr_parts(cur.func)
        if inner:
            parts.append(inner[-1])
            parts.reverse()
            return parts
    return []


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: List[Dict[str, Any]] = []
        self.aliases: Dict[str, str] = {}

    def _add(self, level: str, code: str, message: str, node: ast.AST) -> None:
        self.findings.append({"level": level, "code": code, "message": message,
                              "line": getattr(node, "lineno", 0)})

    def _resolve(self, parts: List[str]) -> str:
        if not parts:
            return ""
        root = parts[0]
        return ".".join([self.aliases.get(root, root)] + parts[1:])

    def _check_module(self, module: str, node: ast.AST) -> None:
        root = (module or "").split(".")[0]
        if not root:
            return
        if root in _NETWORK_MODULES:
            self._add("R2", "network", f"导入联网模块 {module}", node)
        if root in _SECRET_MODULES:
            self._add("R3", "secret", f"导入凭据模块 {module}", node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            self._check_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._check_module(module, node)
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = (
                f"{module}.{alias.name}" if module else alias.name)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = _attr_parts(node)
        if parts and self._resolve(parts) == "os.environ":
            self._add("R3", "secret", "读取环境变量（可能含密钥）", node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        parts = _attr_parts(node.func)
        raw = ".".join(parts)
        full = self._resolve(parts)

        if len(parts) == 1 and parts[0] in _FORBIDDEN_CALLS:
            self._add("FORBID", "dynamic", f"禁止调用 {parts[0]}()", node)

        if raw == "open" and len(node.args) >= 2:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                if any(ch in mode.value for ch in "wax+"):
                    self._add("R1", "write", f"写文件 open(..., '{mode.value}')", node)
            else:
                self._add("R1", "write", "open() 的 mode 无法静态确定", node)

        if full in _SHELL_CALLS:
            level = "R1"
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    level = "R3"
            self._add(level, "shell", f"执行命令 {full}", node)

        root, _, tail = full.partition(".")
        if root in _WRITE_CALLS and tail in _WRITE_CALLS[root]:
            self._add("R1", "write", f"写操作 {full}", node)
        if tail in {"write_text", "write_bytes"}:
            self._add("R1", "write", f"写操作 {full or tail}", node)
        if full.startswith("importlib.import_module"):
            self._add("R1", "dynamic_import", "动态导入模块", node)

        self.generic_visit(node)


def _scan_files(domain_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    findings: List[Dict[str, Any]] = []
    py_files = sorted(domain_dir.glob("*.py"))
    if not py_files:
        return {"passed": False, "risk": "R0", "errors": ["目录内没有 .py 文件"], "findings": []}

    for py in py_files:
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as e:
            errors.append(f"{py.name} 语法错误: 第 {e.lineno} 行 {e.msg}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{py.name} 读取失败: {e}")
            continue
        scanner = _Scanner()
        scanner.visit(tree)
        for f in scanner.findings:
            f["file"] = py.name
            findings.append(f)
            if f["level"] == "FORBID":
                errors.append(f"{py.name}:{f['line']} {f['message']}")

    risk = "R0"
    for f in findings:
        if f["level"] in RISK_ORDER and RISK_ORDER[f["level"]] > RISK_ORDER[risk]:
            risk = f["level"]
    return {"passed": not errors, "risk": risk, "errors": errors, "findings": findings}


# ---------- G2：子进程导入冒烟 ----------

_SMOKE_CODE = """
import json
mod = __import__("LLM_v2.domains.{id}.{id}", fromlist=["*"])
build = getattr(mod, "build_{id}_domain", None)
assert callable(build), "缺少工厂函数 build_{id}_domain()"
d = build()
out = {{
    "id": d.id,
    "label": d.label,
    "prefix": getattr(d, "prefix", ""),
    "tools": list(d.allowed_names()),
    "schema": list(d.tools()),
    "schemas": len(d.tools()),
}}
print("__SMOKE__" + json.dumps(out, ensure_ascii=False))
"""


def _smoke(domain_id: str, timeout: float = G2_TIMEOUT) -> Dict[str, Any]:
    code = _SMOKE_CODE.format(id=domain_id)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(_PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": f"导入超时（>{timeout:.0f}s）"}
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "error": f"启动子进程失败: {e}"}

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__SMOKE__"):
            try:
                payload = json.loads(line[len("__SMOKE__"):])
            except Exception:  # noqa: BLE001
                payload = None
    if proc.returncode != 0 or payload is None:
        tail = ((proc.stderr or "") or (proc.stdout or "")).strip().splitlines()[-8:]
        return {"passed": False, "error": "导入/构造失败", "detail": "\n".join(tail)}
    if not payload.get("tools"):
        return {"passed": False, "error": "域没有暴露任何工具", "payload": payload}
    return {"passed": True, "payload": payload}


# ---------- G3：子进程真调用（冒烟执行） ----------
#
# 只在两种情况下执行工具：
#   1. manifest.json 的 "smoke" 显式给了参数（作者已同意用这些参数跑一次）
#   2. 风险为 R0，且工具所有必填参数都有默认值
# 其余工具记为 skipped，不算失败（避免用瞎编的参数触发写操作/联网）。

def _plan_smoke_tools(risk: str, payload: Dict[str, Any],
                      manifest: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    props_by_tool: Dict[str, Dict[str, Any]] = {}
    for schema in payload.get("schema") or []:
        fn = schema.get("function") or {}
        props_by_tool[fn.get("name", "")] = fn.get("parameters") or {}

    declared = manifest.get("smoke") if isinstance(manifest.get("smoke"), dict) else {}
    plan: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for tool in payload.get("tools") or []:
        if tool in declared and isinstance(declared[tool], dict):
            plan.append({"tool": tool, "args": declared[tool], "mode": "declared"})
            continue

        params = props_by_tool.get(tool) or {}
        # 没有 manifest 就不是"自生成能力"：不主动执行任何工具，
        # 避免对既有域（实现可能藏在 tool/ 下、静态扫描看不到）产生副作用。
        if not manifest:
            skipped.append({"tool": tool, "reason": "缺少 manifest.json，不自动执行"})
            continue
        if risk != "R0":
            skipped.append({"tool": tool, "reason": f"{risk} 且 manifest 未声明 smoke 参数"})
            continue
        required = params.get("required") or []
        props = params.get("properties") or {}
        missing = [r for r in required if "default" not in (props.get(r) or {})]
        if missing:
            skipped.append({"tool": tool,
                            "reason": f"必填参数无默认值: {', '.join(missing)}"})
            continue
        args = {k: v["default"] for k, v in props.items() if "default" in v}
        plan.append({"tool": tool, "args": args, "mode": "defaults"})

    return plan, skipped


def _smoke_tools(domain_id: str, plan: List[Dict[str, Any]],
                 timeout: float = G3_TIMEOUT, attempt: int = 0) -> Dict[str, Any]:
    if not plan:
        return {"passed": True, "executed": [], "note": "没有可安全执行的工具"}

    code = (
        "import asyncio, importlib, inspect, json, socket, traceback\n"
        "socket.setdefaulttimeout(15)\n"
        f"plan = json.loads({json.dumps(plan, ensure_ascii=False)!r})\n"
        f"mod = importlib.import_module('LLM_v2.domains.{domain_id}.{domain_id}')\n"
        f"d = getattr(mod, 'build_{domain_id}_domain')()\n"
        "out = []\n"
        "for item in plan:\n"
        "    tool = item['tool']\n"
        "    args = item.get('args') or {}\n"
        "    try:\n"
        "        fn = getattr(d, tool)\n"
        "        res = asyncio.run(fn(**args)) if inspect.iscoroutinefunction(fn) else fn(**args)\n"
        "        out.append({'tool': tool, 'ok': bool(isinstance(res, dict) and res.get('success')),\n"
        "                    'returned_success': res.get('success') if isinstance(res, dict) else None,\n"
        "                    'type': type(res).__name__, 'preview': str(res)[:240]})\n"
        "    except Exception:\n"
        "        out.append({'tool': tool, 'ok': False, 'error': traceback.format_exc()[-600:]})\n"
        "print('__SMOKE3__' + json.dumps(out, ensure_ascii=False))\n"
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(_PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(_PROJECT_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": f"冒烟执行超时（>{timeout:.0f}s）",
                "executed": [], "timeout_tools": [p["tool"] for p in plan]}
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "error": f"启动子进程失败: {e}", "executed": []}

    results = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__SMOKE3__"):
            try:
                results = json.loads(line[len("__SMOKE3__"):])
            except Exception:  # noqa: BLE001
                results = None
    if results is None:
        tail = ((proc.stderr or "") or (proc.stdout or "")).strip().splitlines()[-8:]
        return {"passed": False, "error": "冒烟执行未返回结果", "detail": "\n".join(tail),
                "executed": []}

    failed = [r for r in results if not r.get("ok")]
    out = {
        "passed": not failed,
        "executed": results,
        "error": ("工具未返回成功结果（异常 / 非 dict / success=False）: " +
                  ", ".join(f"{r['tool']}（{(r.get('error') or (r.get('preview') or ''))[:80]}）"
                            for r in failed)) if failed else "",
    }
    # 网络抖动/瞬时失败重试一次；确定性失败（抛异常）也重试，成本低且能排除偶发
    if failed and attempt < 1:
        _log("INFO", f"{domain_id} G3 有工具失败，重试一次：{[r['tool'] for r in failed]}")
        retry = _smoke_tools(domain_id, plan, timeout=timeout, attempt=attempt + 1)
        retry["retried"] = True
        return retry
    return out


# ---------- 验证入口 ----------

_verify_cache: Dict[str, Dict[str, Any]] = {}


def _dir_signature(domain_dir: Path) -> str:
    """目录内容指纹：任一 .py 或 manifest 变化就失效"""
    items: List[str] = []
    for p in sorted(domain_dir.glob("*.py")) + [domain_dir / "manifest.json"]:
        if p.exists():
            st = p.stat()
            items.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(items)


def _resolve_domain_dir(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def verify_capability(path: str) -> Dict[str, Any]:
    """G1 静态扫描 → G2 子进程导入 → G3 冒烟调用，返回风险等级与闸门明细。

    结果按目录指纹缓存：admit 不会把三道闸门重复跑一遍。
    每个阶段都写实时日志（WebUI 日志页可见），避免长时间静默。
    """
    domain_dir = _resolve_domain_dir(path)
    domain_id = domain_dir.name
    errors: List[str] = []

    if not domain_dir.is_dir():
        return {"passed": False, "id": domain_id, "risk": "R3",
                "errors": [f"目录不存在: {domain_dir}"], "gates": {}}
    if domain_dir.parent != DOMAINS_ROOT.resolve():
        return {"passed": False, "id": domain_id, "risk": "R3",
                "errors": ["域目录必须直接位于 LLM_v2/domains/ 下"], "gates": {}}
    if domain_id == SELF_ID:
        return {"passed": False, "id": domain_id, "risk": "R3",
                "errors": ["不能把管理域自身作为自生成能力"], "gates": {}}
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", domain_id):
        return {"passed": False, "id": domain_id, "risk": "R3",
                "errors": [f"非法域 id: {domain_id}（要求小写字母+下划线）"], "gates": {}}
    if not (domain_dir / f"{domain_id}.py").exists():
        errors.append(f"缺少 {domain_id}.py")

    sig = _dir_signature(domain_dir)
    cached = _verify_cache.get(domain_id)
    if cached and cached.get("sig") == sig:
        _log("INFO", f"{domain_id} 验证结果命中缓存（{cached['result']['risk']}），"
                     f"跳过重复闸门")
        return dict(cached["result"])

    t0 = time.monotonic()
    _log("INFO", f"{domain_id} 开始验证：G1 静态扫描")
    g1 = _scan_files(domain_dir)
    errors.extend(g1["errors"])
    _log("INFO" if g1["passed"] else "WARN",
         f"{domain_id} G1 {'通过' if g1['passed'] else '未通过'}"
         f"（风险初判 {g1['risk']}，{len(g1['findings'])} 条发现，"
         f"{time.monotonic() - t0:.1f}s）")

    # 先读 manifest：声明风险会影响后面的 G3 是否允许真跑
    risk = g1["risk"]
    manifest: Dict[str, Any] = {}
    manifest_path = domain_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared = str(manifest.get("risk", "")).upper()
            if declared in RISK_ORDER and RISK_ORDER[declared] > RISK_ORDER[risk]:
                risk = declared
            if manifest.get("id") and manifest["id"] != domain_id:
                errors.append(f"manifest.id={manifest['id']} 与目录名 {domain_id} 不一致")
        except Exception as e:  # noqa: BLE001
            errors.append(f"manifest.json 解析失败: {e}")

    g2: Dict[str, Any] = {"passed": False, "error": "G1 未通过，跳过 G2"}
    if g1["passed"] and not errors:
        _log("INFO", f"{domain_id} 开始 G2：子进程导入（超时 {G2_TIMEOUT:.0f}s）")
        g2 = _smoke(domain_id)
        _log("INFO" if g2.get("passed") else "WARN",
             f"{domain_id} G2 {'通过' if g2.get('passed') else '未通过'}"
             f"（累计 {time.monotonic() - t0:.1f}s）"
             + ("" if g2.get("passed") else f"：{g2.get('error')}"))
    if g2.get("passed") is False and g2.get("error") != "G1 未通过，跳过 G2":
        errors.append(g2.get("error", "G2 失败") + (
            f": {g2.get('detail')}" if g2.get("detail") else ""))

    g3: Dict[str, Any] = {"passed": False, "error": "G1/G2 未通过，跳过 G3"}
    if g1["passed"] and g2.get("passed"):
        plan, skipped = _plan_smoke_tools(risk, g2.get("payload") or {}, manifest)
        if plan:
            _log("INFO", f"{domain_id} 开始 G3：冒烟调用 "
                         f"{[p['tool'] for p in plan]}（超时 {G3_TIMEOUT:.0f}s）")
        else:
            _log("INFO", f"{domain_id} G3 无可安全执行的工具，跳过")
        g3 = _smoke_tools(domain_id, plan)
        g3["planned"] = [p["tool"] for p in plan]
        g3["skipped"] = skipped
        if not g3.get("passed"):
            errors.append(g3.get("error") or "G3 冒烟执行失败")
        _log("INFO" if g3.get("passed") else "WARN",
             f"{domain_id} G3 {'通过' if g3.get('passed') else '未通过'}"
             f"（{len(g3.get('executed') or [])} 执行 / "
             f"{len(skipped)} 跳过，累计 {time.monotonic() - t0:.1f}s）")

    passed = (g1["passed"] and bool(g2.get("passed")) and bool(g3.get("passed"))
              and not errors)
    result = {
        "passed": passed,
        "id": domain_id,
        "risk": risk,
        "errors": errors,
        "payload": g2.get("payload") or {},
        "gates": {
            "g1_static": {"passed": g1["passed"], "errors": g1["errors"],
                          "findings": g1["findings"]},
            "g2_smoke": g2,
            "g3_tools": g3,
        },
    }
    if passed or g1["passed"]:
        # 只缓存确定性结果，避免把偶发的网络超时永久记住
        _verify_cache[domain_id] = {"sig": sig, "result": result,
                                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _log("INFO" if passed else "WARN",
         f"{domain_id} 验证结束：{'通过' if passed else '未通过'}（{risk}，"
         f"总耗时 {time.monotonic() - t0:.1f}s）")
    return result


# ---------- 挂载 / 回滚 ----------

def _purge_modules(domain_id: str) -> None:
    """清掉主进程里该域的模块缓存，保证恢复后加载的是磁盘版本"""
    prefix = f"LLM_v2.domains.{domain_id}"
    for key in list(sys.modules):
        if key == prefix or key.startswith(prefix + "."):
            sys.modules.pop(key, None)


async def _mount(domain_id: str) -> Dict[str, Any]:
    rt = _runtime()
    asm = getattr(rt, "assembly", None)
    if asm is None:
        return {"success": False, "error": "assembly 未就绪"}
    try:
        importlib.invalidate_caches()
        _purge_modules(domain_id)
        await asm.rediscover()
        world = next((w for w in asm.worlds_status().get("worlds", [])
                      if w["id"] == domain_id), None)
        if world is None:
            return {"success": False, "error": "rediscover 未挂载该域（导入可能失败）"}
        if not world.get("enabled"):
            await asm.start_domain(domain_id)
        return {"success": True}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[capability_ops] 挂载 {domain_id} 失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _snapshot(domain_id: str, domain_dir: Path) -> str:
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = SNAPSHOT_ROOT / domain_id / ts
        SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(domain_dir, dest,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return str(dest)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 快照失败 {domain_id}: {e}")
        return ""


def _rollback(domain_id: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    domain_dir = DOMAINS_ROOT / domain_id
    try:
        if rec.get("created_new"):
            if domain_dir.exists():
                ts = time.strftime("%Y%m%d-%H%M%S")
                dest = QUARANTINE_ROOT / f"{domain_id}-{ts}"
                QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)
                shutil.move(str(domain_dir), str(dest))
                return {"quarantine_path": str(dest), "rollback": "quarantined"}
            return {"rollback": "none"}
        snapshot = rec.get("snapshot") or ""
        if snapshot and Path(snapshot).is_dir():
            shutil.copytree(snapshot, domain_dir, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            return {"rollback": "restored_snapshot"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 回滚 {domain_id} 失败: {e}")
        return {"rollback": f"failed: {e}"}
    return {"rollback": "disabled_only"}


# ---------- 准入 ----------

def _new_record(capability_id: str, domain_dir: Path, result: Dict[str, Any],
                status: str, source: str, session_id: Optional[int],
                created_new: bool) -> Dict[str, Any]:
    old = _get_record(capability_id) or {}
    return {
        "id": capability_id,
        "label": (result.get("payload") or {}).get("label") or old.get("label") or capability_id,
        "dir": str(domain_dir),
        "risk": result.get("risk", "R3"),
        "status": status,
        "source": source,
        "session_id": session_id,
        "tools": (result.get("payload") or {}).get("tools", []),
        "created_at": old.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "admitted_at": old.get("admitted_at"),
        "suspended_at": None,
        "reason": "",
        "created_new": created_new,
        "snapshot": old.get("snapshot", ""),
        "quarantine_path": old.get("quarantine_path", ""),
        "budget_calls": old.get("budget_calls", BUDGET_CALLS_DEFAULT),
        "stage": old.get("stage") or "derived",
        "stage_at": old.get("stage_at"),
        "stage_note": old.get("stage_note", ""),
        "stats": old.get("stats") or {
            "calls": 0, "success": 0, "failures": 0,
            "consecutive_failures": 0, "avg_ms": 0, "last_ms": 0,
            "last_used": None, "recent": [],
        },
        "gates": {
            "g1_static": {"passed": (result.get("gates") or {}).get("g1_static", {}).get("passed")},
            "g2_smoke": {"passed": (result.get("gates") or {}).get("g2_smoke", {}).get("passed")},
            "g3_tools": {
                "passed": (result.get("gates") or {}).get("g3_tools", {}).get("passed"),
                "executed": [
                    {"tool": r.get("tool"), "ok": r.get("ok"),
                     "returned_success": r.get("returned_success")}
                    for r in ((result.get("gates") or {}).get("g3_tools", {}).get("executed") or [])
                ],
                "skipped": (result.get("gates") or {}).get("g3_tools", {}).get("skipped", []),
            },
        },
    }


def _known_domain_ids() -> set:
    try:
        rt = _runtime()
        asm = getattr(rt, "assembly", None)
        return {w["id"] for w in asm.worlds_status().get("worlds", [])} if asm else set()
    except Exception:  # noqa: BLE001
        return set()


def _restore_from_quarantine(capability_id: str,
                             rec: Optional[Dict[str, Any]] = None) -> bool:
    """把被隔离的域目录移回 domains/（人工确认后重新准入时用）"""
    rec = rec if rec is not None else (_get_record(capability_id) or {})
    quarantine = rec.get("quarantine_path") or ""
    dest = DOMAINS_ROOT / capability_id
    try:
        if quarantine and Path(quarantine).exists() and not dest.exists():
            shutil.move(quarantine, str(dest))
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 恢复隔离目录 {capability_id} 失败: {e}")
    return False


async def enforce_registry() -> List[Dict[str, Any]]:
    """启动时执行登记表：被拒绝/待确认的能力不得随装配自动生效。

    背景：DomainAssembly 启动时会自动发现 domains/ 下所有目录，
    准入判定只发生在运行期。若不在启动后纠正，一个被拒绝的 R2/R3 能力
    会在下次重启时被静默挂载。这里停用并把目录移入隔离区。
    """
    actions: List[Dict[str, Any]] = []
    try:
        rt = _runtime()
        asm = getattr(rt, "assembly", None)
    except Exception:  # noqa: BLE001
        return actions

    capabilities = list_capabilities()["capabilities"]
    recorded = {c.get("id"): c for c in capabilities}

    # 从未登记、但带 manifest.json 的目录 = 自生成能力，还没走准入 → 启动时不得生效
    try:
        for entry in sorted(os.listdir(DOMAINS_ROOT)):
            d = DOMAINS_ROOT / entry
            if (not d.is_dir() or entry.startswith("__") or entry.startswith(".")):
                continue
            if entry in recorded or entry == SELF_ID:
                continue
            manifest_path = d / "manifest.json"
            if not manifest_path.exists():
                continue
            # builtin=true 是随项目发布的正式能力域，不属于"AI 自生成待审"
            try:
                declared = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                declared = {}
            if isinstance(declared, dict) and declared.get("builtin"):
                continue
            if (d / f"{entry}.py").exists():
                recorded[entry] = {
                    "id": entry, "status": "pending_review",
                    "reason": "启动时发现：有 manifest 但未登记准入",
                    "created_new": True, "dir": str(d),
                }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 扫描未登记能力失败: {e}")

    for rec in recorded.values():
        status = rec.get("status")
        if status not in ("pending_review", "rejected", "suspended"):
            continue
        cid = rec.get("id")
        if not cid:
            continue
        if asm is not None:
            try:
                world = next((w for w in asm.worlds_status().get("worlds", [])
                              if w["id"] == cid), None)
                if world and world.get("enabled"):
                    await asm.stop_domain(cid)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[capability_ops] 启动停用 {cid} 失败: {e}")

        domain_dir = DOMAINS_ROOT / cid
        # 注意：这里只「停用」，不移动目录。
        # 被拒绝/待确认的能力需要保留文件，才能让 AI 修好后重新准入；
        # 只有熔断回滚（suspend）才把目录移进隔离区。
        if status in ("pending_review", "rejected") and domain_dir.is_dir():
            try:
                if _get_record(cid) is None:
                    declared = {}
                    try:
                        declared = json.loads(
                            (domain_dir / "manifest.json").read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        declared = {}
                    _upsert_record({
                        "id": cid,
                        "label": declared.get("label") or cid,
                        "dir": str(domain_dir),
                        "risk": str(declared.get("risk", "R3")).upper(),
                        "status": "pending_review",
                        "source": "startup_scan",
                        "session_id": None,
                        "tools": [],
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "admitted_at": None,
                        "suspended_at": None,
                        "reason": "启动时发现：有 manifest 但未登记准入",
                        "created_new": True,
                        "snapshot": "",
                        "quarantine_path": "",
                        "budget_calls": BUDGET_CALLS_DEFAULT,
                        "stats": {"calls": 0, "success": 0, "failures": 0,
                                  "consecutive_failures": 0, "avg_ms": 0,
                                  "last_ms": 0, "last_used": None},
                        "gates": {},
                    })
                actions.append({"id": cid, "action": "disabled_only"})
                _log("WARN", f"能力 {cid}（{status}）启动时已停用，文件保留供重新准入")
            except Exception as e:  # noqa: BLE001
                actions.append({"id": cid, "action": "disable_failed", "error": str(e)})
        else:
            actions.append({"id": cid, "action": "disabled"})
    return actions


async def admit_capability(path: str, force: bool = False,
                           source: str = "explicit", session_id: Optional[int] = None,
                           require_manifest: bool = False) -> Dict[str, Any]:
    """验证 → 风险判定 → 自动挂载（R0/R1）

    force=True 放宽风险上限（仍要求 G1/G2/G3 通过）；
    require_manifest=True 用于自动触发路径（manifest.json 作为"这是能力"的显式标记）。
    """
    domain_dir = _resolve_domain_dir(path)
    domain_id = domain_dir.name

    # 之前被隔离的能力，人工重新准入时先把目录移回来
    if not domain_dir.exists():
        _restore_from_quarantine(domain_id)
        domain_dir = _resolve_domain_dir(path)

    if require_manifest and not (domain_dir / "manifest.json").exists():
        return {"success": False, "id": domain_id, "status": "skipped",
                "reason": "缺少 manifest.json（自动触发要求显式声明）"}

    result = verify_capability(path)
    created_new = domain_id not in _known_domain_ids()

    if not result["passed"]:
        rec = _new_record(domain_id, domain_dir, result, "rejected", source,
                          session_id, created_new)
        rec["reason"] = "；".join(result["errors"][:3]) or "闸门未通过"
        _upsert_record(rec)
        _log("WARN", f"能力 {domain_id} 被拒绝：{rec['reason']}")
        return {"success": False, "id": domain_id, "status": "rejected",
                "risk": result["risk"], "errors": result["errors"],
                "gates": result["gates"]}

    # 登记一次即信任：人工准入过的能力不再逐次确认风险
    existing = (_load_registry().get("capabilities") or {}).get(domain_id) or {}
    trusted = bool(existing.get("trusted"))
    limit = "R3" if (force or trusted) else _risk_limit()
    if RISK_ORDER.get(result["risk"], 9) > RISK_ORDER.get(limit, 1):
        rec = _new_record(domain_id, domain_dir, result, "pending_review", source,
                          session_id, created_new)
        rec["reason"] = f"风险 {result['risk']} 超过自动准入上限 {limit}，需人工确认"
        _upsert_record(rec)
        _log("WARN", f"能力 {domain_id} 需人工确认（{result['risk']}）")
        return {"success": False, "id": domain_id, "status": "pending_review",
                "risk": result["risk"], "errors": [], "gates": result["gates"]}

    rec = _new_record(domain_id, domain_dir, result, "staging", source,
                      session_id, created_new)
    if not created_new:
        rec["snapshot"] = _snapshot(domain_id, domain_dir)
    _upsert_record(rec)

    mounted = await _mount(domain_id)
    if not mounted.get("success"):
        rec["status"] = "rejected"
        rec["reason"] = mounted.get("error", "挂载失败")
        _upsert_record(rec)
        _log("WARN", f"能力 {domain_id} 挂载失败：{rec['reason']}")
        return {"success": False, "id": domain_id, "status": "rejected",
                "risk": result["risk"], "error": rec["reason"]}

    _update_record(domain_id, status="active",
                   admitted_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                   reason="", quarantine_path="",
                   trusted=True)
    _log("INFO", f"能力 {domain_id} 已准入（{result['risk']}，"
                 f"{len(result['payload'].get('tools', []))} 个工具，已登记信任）")
    return {"success": True, "id": domain_id, "status": "active",
            "risk": result["risk"], "tools": result["payload"].get("tools", []),
            "gates": result["gates"]}


# ---------- 熔断 ----------

def note_tool_result(domain_id: str, tool: str, success: bool, elapsed_ms: int) -> str:
    """记录一次调用；返回非空字符串表示应熔断"""
    if domain_id == SELF_ID:
        return ""
    rec = _get_record(domain_id)
    if rec is None or rec.get("status") not in ("active", "staging"):
        return ""

    with _registry_lock:
        data = _load_registry()
        rec = data["capabilities"].get(domain_id)
        if rec is None:
            return ""
        st = rec.setdefault("stats", {})
        calls = int(st.get("calls", 0)) + 1
        wins = int(st.get("success", 0)) + (1 if success else 0)
        st["calls"] = calls
        st["success"] = wins
        st["failures"] = calls - wins
        st["consecutive_failures"] = 0 if success else int(st.get("consecutive_failures", 0)) + 1
        st["last_ms"] = int(elapsed_ms)
        st["avg_ms"] = int(((st.get("avg_ms", 0) or 0) * (calls - 1) + elapsed_ms) / calls)
        st["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
        # 最近 20 次成败，用于漂移检测（固定能力不自动降级）
        recent = (list(st.get("recent") or []) + [1 if success else 0])[-20:]
        st["recent"] = recent

        stage = rec.get("stage") or "derived"
        stage_now = time.strftime("%Y-%m-%d %H:%M:%S")
        drift_reason = ""
        if stage != "fixed":
            rate = wins / calls if calls else 0.0
            if (stage == "derived" and calls >= CAPTURE_MIN_CALLS
                    and rate >= CAPTURE_MIN_RATE):
                rec["stage"] = "captured"
                rec["stage_at"] = stage_now
                rec["stage_note"] = f"连续成功（{wins}/{calls}），自动固化"
                _log("INFO", f"能力 {domain_id} 晋升 captured（{wins}/{calls}）")
            elif stage == "captured":
                window = recent[-DRIFT_WINDOW:]
                if len(window) >= DRIFT_WINDOW and (sum(window) / len(window)) < DRIFT_MIN_RATE:
                    rec["stage"] = "derived"
                    rec["stage_at"] = stage_now
                    rec["stage_note"] = "漂移：最近成功率偏低，需重走闸门"
                    drift_reason = (f"漂移：最近 {len(window)} 次成功率 "
                                    f"{int(sum(window) / len(window) * 100)}% "
                                    f"低于 {int(DRIFT_MIN_RATE * 100)}%")
                    _log("WARN", f"能力 {domain_id} 漂移降级：{drift_reason}")
        rec.setdefault("stage", stage)
        _save_registry(data)

    if int(st.get("consecutive_failures", 0)) >= MAX_CONSECUTIVE_FAILURES:
        return f"连续 {MAX_CONSECUTIVE_FAILURES} 次失败"
    if calls >= MIN_CALLS_FOR_RATE and (wins / calls) < MIN_SUCCESS_RATE:
        return f"成功率 {wins}/{calls} 低于 {int(MIN_SUCCESS_RATE * 100)}%"
    if calls > int(rec.get("budget_calls", BUDGET_CALLS_DEFAULT)):
        return f"超出调用预算 {rec.get('budget_calls')}"
    return drift_reason


def set_stage(capability_id: str, stage: str, note: str = "") -> Dict[str, Any]:
    """人工/自动设置能力演化阶段：derived / captured / fixed"""
    if stage not in ("derived", "captured", "fixed"):
        return {"success": False, "error": f"未知阶段 {stage}"}
    rec = _get_record(capability_id)
    if rec is None:
        return {"success": False, "error": f"能力 {capability_id} 未登记"}
    rec = _update_record(
        capability_id, stage=stage, stage_note=note or "manual",
        stage_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    _log("INFO", f"能力 {capability_id} 阶段 → {stage}（{note or 'manual'}）")
    return {"success": True, "id": capability_id, "stage": (rec or {}).get("stage", stage)}


async def suspend_capability(capability_id: str, reason: str) -> Dict[str, Any]:
    """停用 + 回滚：新建能力移入隔离区，已有能力恢复准入前快照"""
    rec = _get_record(capability_id) or {}
    if rec.get("status") == "suspended":
        return {"success": True, "id": capability_id, "status": "suspended"}

    try:
        rt = _runtime()
        asm = getattr(rt, "assembly", None)
        if asm is not None:
            await asm.stop_domain(capability_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[capability_ops] 停用 {capability_id} 失败: {e}")

    rollback = _rollback(capability_id, rec)
    _update_record(capability_id, status="suspended", reason=reason,
                   suspended_at=time.strftime("%Y-%m-%d %H:%M:%S"), **rollback)
    _log("WARN", f"能力 {capability_id} 已熔断停用：{reason}（{rollback.get('rollback')}）")
    return {"success": True, "id": capability_id, "status": "suspended",
            "reason": reason, "rollback": rollback.get("rollback")}


async def resume_capability(capability_id: str) -> Dict[str, Any]:
    """人工恢复：隔离目录移回（或沿用现有目录）后重新挂载，并清零熔断计数"""
    rec = _get_record(capability_id)
    if rec is None:
        return {"success": False, "error": f"未知能力: {capability_id}"}

    try:
        _restore_from_quarantine(capability_id, rec)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"恢复目录失败: {e}"}

    _update_record(capability_id, status="staging", reason="", quarantine_path="",
                   stats={**(rec.get("stats") or {}), "consecutive_failures": 0})
    mounted = await _mount(capability_id)
    if not mounted.get("success"):
        _update_record(capability_id, status="suspended",
                       reason=mounted.get("error", "挂载失败"))
        return {"success": False, **mounted}
    _update_record(capability_id, status="active",
                   admitted_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    _log("INFO", f"能力 {capability_id} 已恢复")
    return {"success": True, "id": capability_id, "status": "active"}


# ---------- 查询 / 调用 ----------

def list_capabilities() -> Dict[str, Any]:
    with _registry_lock:
        caps = list(_load_registry()["capabilities"].values())
    caps.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    for rec in caps:
        rec.setdefault("stage", "derived")
    return {
        "capabilities": caps,
        "auto_risk": _risk_limit(),
        "auto_admit": _auto_admit_enabled(),
        "thresholds": {
            "max_consecutive_failures": MAX_CONSECUTIVE_FAILURES,
            "min_calls_for_rate": MIN_CALLS_FOR_RATE,
            "min_success_rate": MIN_SUCCESS_RATE,
            "budget_calls": BUDGET_CALLS_DEFAULT,
            "capture_min_calls": CAPTURE_MIN_CALLS,
            "capture_min_rate": CAPTURE_MIN_RATE,
            "drift_window": DRIFT_WINDOW,
            "drift_min_rate": DRIFT_MIN_RATE,
        },
    }


async def call_capability(tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """经管理域调用自生成能力：先查熔断状态，再委托 assembly.call_tool"""
    args = arguments or {}
    owner = None
    with _registry_lock:
        for rec in _load_registry()["capabilities"].values():
            if tool in (rec.get("tools") or []):
                owner = rec
                break
    if owner is None:
        return {"success": False, "error": f"不是已登记的自生成能力工具: {tool}"}
    if owner.get("status") != "active":
        return {"success": False, "error": f"能力 {owner['id']} 当前状态 {owner.get('status')}，已拒绝调用"}

    rt = _runtime()
    try:
        return await rt.assembly.call_tool(tool, args, timeout=120.0)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


# ---------- 自动触发（TASK_DONE）----------

def find_generated_domain_dirs(result: Dict[str, Any]) -> List[Path]:
    """从编程会话结果里找出落在 LLM_v2/domains/<id>/ 的改动"""
    files = result.get("files_changed") or []
    workspace = result.get("workspace") or str(_PROJECT_ROOT)
    found: List[Path] = []
    for raw in files:
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = Path(workspace) / p
            rel = p.resolve().relative_to(DOMAINS_ROOT.resolve())
        except Exception:  # noqa: BLE001
            continue
        if len(rel.parts) >= 2:
            d = DOMAINS_ROOT / rel.parts[0]
            if d.is_dir() and d not in found:
                found.append(d)
    return found


async def auto_admit_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """编程任务成功收尾后，自动准入新生成的能力域（R0/R1 免点击）"""
    if not _auto_admit_enabled():
        return []
    outcomes: List[Dict[str, Any]] = []
    for d in find_generated_domain_dirs(result):
        if d.name == SELF_ID:
            continue
        outcomes.append(await admit_capability(
            str(d), source="coding_loop",
            session_id=result.get("session_id"), require_manifest=True))
    return outcomes
