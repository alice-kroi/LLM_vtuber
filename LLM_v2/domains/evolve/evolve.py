# ============================================================
# LLM_v2/domains/evolve/evolve.py — 自进化能力域
# ============================================================
# 参考 DSH dsh-evolve 的机制，映射到 LLM_vtuber V2：
#   - AI 用 evolve_add 生成一个独立能力域目录（源码 + manifest + schema）
#   - 走 capability_ops 的 G1/G2/G3 准入，通过后热挂载
#   - 新工具下一轮即可被 ToolAgent 看到
#   - evolve_remove 停用 + 反注册 + 删除源码
#   - 目录天然持久化，重启由 DomainAssembly 自动发现
#
# 信任模型与 dsh-evolve 一致：生成代码与主进程同权限，仅用于可信自用环境。
# ============================================================

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.evolve.evolve_tools import (
    EVOLVE_TOOL_NAMES,
    EVOLVE_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DOMAINS_ROOT = _ROOT / "LLM_v2" / "domains"

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
_TOOL_RE = re.compile(r"^evo_[a-z0-9_]{2,48}$")

_CN_STOP = {
    "工具", "能力", "用于", "可以", "一个", "这个", "那个", "进行", "通过",
    "获取", "查询", "计算", "转换", "支持", "输入", "输出", "返回", "结果",
    "用户", "需要", "如果", "以及", "相关", "数据", "信息", "内容", "操作",
    "方法", "函数", "使用", "时候", "什么", "怎么", "是否", "并且", "或者",
    "然后", "现在", "已经", "没有", "我们", "你们", "他们", "任务", "功能",
}
_EN_STOP = {
    "the", "and", "for", "with", "tool", "function", "returns", "return",
    "input", "output", "user", "using", "used", "this", "that", "from",
    "into", "can", "will", "should", "value", "values", "result", "results",
}


def _name_variant(a: str, b: str) -> bool:
    """工具名变体：一个是另一个的子串且长度接近（如 xxx_v2）。"""
    a = str(a or "").lower()
    b = str(b or "").lower()
    if not a or not b or a == b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return short in long and len(short) / max(1, len(long)) >= 0.72


def _semantic_tokens(text: str) -> set:
    """中英混合的轻量语义 token：英文词 + 中文二元组。"""
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", raw)) - _EN_STOP
    grams: set = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", raw):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in _CN_STOP:
                grams.add(gram)
    return words | grams


CODE_SYSTEM = """你是自进化工具作者。根据用户需求生成一个 Python handler.py。

硬约束：
1. 只输出 JSON，不要代码块、不要解释：
{"description": "什么时候调用这个工具", "parameters": {...}, "required": [...], "code": "完整 handler.py 源码"}
2. code 必须定义 def run(**kwargs): ...，返回 dict 且包含 success（bool）。
3. parameters 是 JSON Schema properties：参数名 -> {"type": "string|integer|number|boolean|array|object", "description": "..."}。
4. 只使用 Python 标准库；确需联网可用 urllib/requests。禁止 os.system、subprocess、删除用户文件、eval/exec。
5. 顶层只放 import 与常量，不要执行副作用；所有副作用放在 run 内。
6. 用户的私有信息（城市、日期、路径、别名）直接写进代码默认值，避免以后每次再传。
7. 出错时返回 {"success": false, "error": "..."}，不要抛出未捕获异常。
"""

PARAMS_SYSTEM = """你是自进化工具作者。用户已提供 handler.py 源码，现在只需要补齐工具元信息。

只输出 JSON，不要代码块：
{"description": "什么时候调用这个工具", "parameters": {...}, "required": [...]}

parameters 是 JSON Schema properties，必须与源码里的 run(**kwargs) 参数一致。
"""


class EvolveDomain(Actuator):
    """自进化能力域：生成、准入、挂载、卸载 AI 自己写的工具。"""

    id = "evolve"
    label = "自进化"
    prefix = "evolve_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    tool_timeouts = {
        "evolve_add": 180.0,
        "evolve_remove": 40.0,
        "evolve_list": 5.0,
    }

    # ---------- 域契约 ----------

    def allowed_names(self) -> List[str]:
        return list(EVOLVE_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in EVOLVE_TOOLS_SCHEMA
                if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return (
            "【自进化】evolve_add 可以给自己新增可持久化工具：AI 生成 handler.py，"
            "经 G1/G2/G3 准入后热挂载，新工具下一轮可用，重启后保留；"
            "evolve_remove 卸载并删除源码；evolve_list 查看已有进化。"
            "只把反复出现、当前没有的能力做成工具；一次性需求不要进化。"
        )

    def central_card(self) -> Dict[str, Any]:
        names = [
            p.get("name") for p in self._list_evolutions()
            if p.get("name")
        ]
        return {
            "summary": f"自进化：{len(names)} 个 AI 生成工具"
                       + (f"（{'、'.join(names[:5])}）" if names else ""),
            "channels": [{
                "name": "自进化清单",
                "tools": ["evolve_list"],
                "description": "查看所有 AI 生成工具",
            }],
        }

    # ---------- 工具实现 ----------

    async def evolve_add(
        self,
        goal: str = "",
        name: str = "",
        tool_name: str = "",
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        code: str = "",
        risk: str = "R2",
        force: bool = False,
    ) -> Dict[str, Any]:
        goal = str(goal or "").strip()
        name = re.sub(r"[^a-z0-9-]+", "-",
                      str(name or "").strip().lower()).strip("-")
        if not goal:
            return {"success": False, "error": "缺少 goal（这个工具要解决什么问题）"}
        if not _NAME_RE.match(name):
            return {"success": False,
                    "error": "name 必须是 kebab-case：小写字母开头，只含小写字母/数字/连字符"}

        domain_id = "evo_" + name.replace("-", "_")
        tool_name = re.sub(
            r"[^a-z0-9_]+", "_",
            str(tool_name or "").strip().lower()).strip("_") or domain_id
        if not _TOOL_RE.match(tool_name):
            return {"success": False,
                    "error": "tool_name 必须以 evo_ 开头，只含小写字母/数字/下划线"}
        risk = str(risk or "R2").strip().upper()
        if risk not in ("R0", "R1", "R2", "R3"):
            risk = "R2"

        asm = self._assembly()
        if asm is None:
            return {"success": False, "error": "assembly 未就绪"}

        existing = domain_id in self._registered_ids(asm)
        old_tool = ""
        if existing:
            try:
                old_tool = str(json.loads(
                    (_DOMAINS_ROOT / domain_id / "manifest.json")
                    .read_text(encoding="utf-8")).get("tool") or "")
            except Exception:  # noqa: BLE001
                old_tool = ""
        if tool_name in set(asm.active_tool_names()) and tool_name != old_tool:
            return {"success": False,
                    "error": f"工具名已存在: {tool_name}，请换一个 tool_name"}

        # 语义去重：默认阻止“已有同义能力还再长一个”
        dup = self._semantic_duplicates(goal, name, tool_name, description, asm)
        force_flag = force is True or str(force).lower() in ("1", "true", "yes", "on")
        if dup.get("duplicate") and not force_flag:
            top = dup["matches"][0]
            return {
                "success": False,
                "duplicate": True,
                "matches": dup["matches"],
                "error": (
                    f"语义重复：已有工具 {top['tool']}"
                    f"（相似度 {top['score']:.2f}，{top['reason']}）"),
                "suggestion": (
                    "优先直接调用已有工具；如果确实要做不同的新工具，"
                    "换一个明确不同的目标，或传 force=true 强制新增"),
            }

        # 生成缺失的元信息 / 源码
        try:
            meta = await self._complete_meta(
                goal, name, tool_name, description, parameters, code)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"工具生成失败: {e}"}

        code = str(meta.get("code") or code or "").strip()
        description = str(meta.get("description") or description or "").strip()
        parameters = meta.get("parameters") if isinstance(
            meta.get("parameters"), dict) else (parameters or {})
        required = [str(x) for x in (meta.get("required") or [])]
        # 容忍模型把完整 object schema 塞进 parameters
        if (isinstance(parameters, dict)
                and parameters.get("type") == "object"
                and isinstance(parameters.get("properties"), dict)):
            required = required or [str(x) for x in (parameters.get("required") or [])]
            parameters = parameters["properties"]
        if not code:
            return {"success": False, "error": "没有生成任何 handler 代码"}
        try:
            self._validate_code(code)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"生成的代码不可用: {e}"}

        domain_dir = _DOMAINS_ROOT / domain_id
        rev = 1
        if domain_dir.is_dir():
            try:
                old = json.loads(
                    (domain_dir / "manifest.json").read_text(encoding="utf-8"))
                rev = int(old.get("rev") or 1) + 1
            except Exception:  # noqa: BLE001
                rev = 1

        # 替换旧版本：先卸载，再写新文件
        if existing:
            await self._unmount(domain_id, delete_source=True, forget=True)

        self._write_evolution(
            domain_id=domain_id,
            name=name,
            tool_name=tool_name,
            description=description,
            parameters=parameters,
            required=required,
            code=code,
            risk=risk,
            rev=rev,
        )

        # 子进程导入检查（handler 顶层有副作用会在这里暴露）
        check = self._import_check(domain_dir / "handler.py")
        if not check.get("success"):
            return {
                "success": False,
                "error": f"handler 导入检查失败: {check.get('error')}",
                "file": str(domain_dir / "handler.py"),
                "note": "源码已保留，修好后用同名再次 evolve_add 即可替换",
            }

        # 走既有准入：G1 静态扫描 / G2 子进程导入 / G3 冒烟（无 smoke 则跳过）
        from LLM_v2.domains.capability_ops.capability_ops_impl import (
            admit_capability,
        )
        admitted = await admit_capability(
            str(domain_dir), force=False, source="evolve",
            require_manifest=True)
        status = admitted.get("status")

        if not admitted.get("success"):
            return {
                "success": False,
                "domain_id": domain_id,
                "tool_name": tool_name,
                "status": status,
                "risk": admitted.get("risk"),
                "error": admitted.get("reason") or admitted.get("error")
                         or "准入未通过",
                "errors": admitted.get("errors") or [],
                "file": str(domain_dir / "handler.py"),
                "note": ("R3 能力需要人工确认，可在能力域控制台准入；"
                         "其他错误可修好源码后同名重试"),
            }

        return {
            "success": True,
            "domain_id": domain_id,
            "tool_name": tool_name,
            "status": status or "active",
            "risk": admitted.get("risk"),
            "rev": rev,
            "replaced": existing,
            "file": str(domain_dir / "handler.py"),
            "note": (
                f"同名进化已替换为 rev {rev}；新版本从下一轮起生效，重启后自动恢复"
                if existing else
                "新工具从下一轮起可用；已持久化，重启后自动恢复"),
            "duplicate_warning": dup.get("matches") if dup.get("duplicate") else [],
        }

    async def evolve_remove(self, name: str = "",
                            keep_source: bool = False) -> Dict[str, Any]:
        name = str(name or "").strip().lower()
        if not name:
            return {"success": False, "error": "缺少 name"}
        domain_id = name if name.startswith("evo_") else (
            "evo_" + name.replace("-", "_"))
        if not (_DOMAINS_ROOT / domain_id).is_dir() and domain_id not in self._registered_ids(self._assembly()):
            return {"success": False, "error": f"没有这个自进化工具: {name}"}
        ok = await self._unmount(
            domain_id, delete_source=not bool(keep_source), forget=True)
        return {"success": ok, "domain_id": domain_id,
                "source_kept": bool(keep_source),
                "error": None if ok else "卸载失败"}

    async def evolve_list(self) -> Dict[str, Any]:
        asm = self._assembly()
        states = {}
        if asm is not None:
            for w in asm.worlds_status().get("worlds", []):
                states[w.get("id")] = w
        items = []
        for item in self._list_evolutions():
            world = states.get(item.get("domain_id")) or {}
            item["state"] = ("active" if world.get("started") else
                             "inactive" if world.get("enabled") else
                             "not-mounted")
            items.append(item)
        return {"success": True, "count": len(items), "evolutions": items}

    # ---------- 生成 / 校验 ----------

    async def _complete_meta(self, goal: str, name: str, tool_name: str,
                             description: str, parameters: Optional[Dict[str, Any]],
                             code: str) -> Dict[str, Any]:
        need_code = not code.strip()
        need_params = not isinstance(parameters, dict) or not parameters
        if not need_code and not need_params and description:
            return {}
        from LLM_v2.core.llm_adapter import get_llm_for
        llm = get_llm_for("coding")
        if need_code:
            user = (
                f"进化名: {name}\n工具名: {tool_name}\n"
                f"用户需求: {goal}\n"
                f"已有说明: {description or '无'}\n"
                "生成 description、parameters、required、code。"
            )
            system = CODE_SYSTEM
        else:
            user = (
                f"用户需求: {goal}\n工具名: {tool_name}\n"
                f"已有说明: {description or '无'}\n"
                f"handler.py 源码:\n{code}\n"
                "只补齐 description、parameters、required。"
            )
            system = PARAMS_SYSTEM
        res = await asyncio.wait_for(
            llm.chat(system_prompt=system, user_text=user,
                     history=[], remember=False,
                     max_tokens=2800, temperature=0.2),
            timeout=120.0,
        )
        if res.get("error"):
            raise RuntimeError(str(res["error"]))
        meta = _parse_json(res.get("response") or "")
        if meta is None:
            raise RuntimeError("模型返回的不是合法 JSON")
        if not need_code:
            meta.pop("code", None)
        return meta

    @staticmethod
    def _semantic_duplicates(goal: str, name: str, tool_name: str,
                             description: str, asm) -> Dict[str, Any]:
        """轻量语义去重：对比现有工具名/描述，找同义能力。

        不引入 embedding 依赖：中文二元组 + 英文词 + 名称变体。
        命中时默认阻止新增，force=true 可覆盖。
        """
        try:
            schemas = asm.active_tool_schemas()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[evolve] 语义去重跳过（schema 不可用）: {e}")
            return {"duplicate": False, "matches": []}

        new_text = " ".join([
            str(goal or ""), str(description or ""),
            str(name or "").replace("-", " "),
            str(tool_name or "").replace("_", " "),
        ])
        new_tokens = _semantic_tokens(new_text)
        if not new_tokens:
            return {"duplicate": False, "matches": []}

        matches: List[Dict[str, Any]] = []
        for schema in schemas:
            if not isinstance(schema, dict):
                continue
            fn = schema.get("function") or {}
            other = str(fn.get("name") or "")
            if not other or other == tool_name:
                continue
            other_desc = str(fn.get("description") or "")
            other_tokens = _semantic_tokens(
                f"{other.replace('_', ' ')} {other_desc}")
            if not other_tokens:
                continue
            shared = new_tokens & other_tokens
            containment = len(shared) / max(1, min(
                len(new_tokens), len(other_tokens)))
            jaccard = len(shared) / max(1, len(new_tokens | other_tokens))
            score = max(jaccard, containment * 0.7 + jaccard * 0.3)
            name_dup = _name_variant(other, tool_name)
            phrase_dup = (other in str(goal or "")) or (
                other in str(description or ""))
            if (name_dup or phrase_dup
                    or (len(shared) >= 3 and containment >= 0.35)
                    or (len(shared) >= 4 and jaccard >= 0.45)):
                if name_dup:
                    reason = "工具名高度相似"
                elif phrase_dup:
                    reason = "目标中直接提到该工具"
                else:
                    reason = f"描述重合 {len(shared)} 个语义片段"
                matches.append({
                    "tool": other,
                    "score": round(score, 3),
                    "reason": reason,
                    "description": other_desc[:120],
                })
        matches.sort(key=lambda x: -x["score"])
        return {"duplicate": bool(matches), "matches": matches[:3]}

    @staticmethod
    def _validate_code(code: str) -> None:
        tree = ast.parse(code, filename="handler.py")
        if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == "run" for node in tree.body):
            raise ValueError("handler.py 顶层必须定义 run(**kwargs)")
        compile(code, "handler.py", "exec")

    @staticmethod
    def _import_check(handler: Path) -> Dict[str, Any]:
        script = (
            "import importlib.util, sys\n"
            "p = sys.argv[1]\n"
            "spec = importlib.util.spec_from_file_location('evo_check', p)\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "assert callable(getattr(m, 'run', None)), 'missing run'\n"
            "print('ok')\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script, str(handler)],
                cwd=str(_ROOT), capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "导入超时（25s）"}
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return {"success": False, "error": err[-800:]}
        return {"success": True}

    # ---------- 文件写入 / 卸载 ----------

    def _write_evolution(self, *, domain_id: str, name: str, tool_name: str,
                         description: str, parameters: Dict[str, Any],
                         required: List[str], code: str, risk: str,
                         rev: int) -> None:
        domain_dir = _DOMAINS_ROOT / domain_id
        domain_dir.mkdir(parents=True, exist_ok=True)
        label = (description or name)[:30]

        manifest = {
            "id": domain_id,
            "label": label,
            "version": "0.1.0",
            "risk": risk,
            "builtin": False,
            "evolved": True,
            "description": description,
            "rev": rev,
            "tool": tool_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tool = {
            "name": tool_name,
            "description": description or f"{label}（AI 自进化工具）",
            "parameters": parameters,
            "required": required,
            "timeout": 30,
        }
        module = (
            "from pathlib import Path\n"
            "from LLM_v2.domains.evolve.evolved_domain import build_evolved_domain\n"
            "\n"
            "_DIR = Path(__file__).resolve().parent\n"
            "\n"
            f"def build_{domain_id}_domain():\n"
            "    return build_evolved_domain(_DIR)\n"
        )
        (domain_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (domain_dir / "tool.json").write_text(
            json.dumps(tool, ensure_ascii=False, indent=2), encoding="utf-8")
        (domain_dir / "handler.py").write_text(
            code.rstrip() + "\n", encoding="utf-8")
        (domain_dir / f"{domain_id}.py").write_text(module, encoding="utf-8")
        (domain_dir / "__init__.py").write_text("", encoding="utf-8")

    async def _unmount(self, domain_id: str, *, delete_source: bool,
                       forget: bool) -> bool:
        asm = self._assembly()
        if asm is not None:
            if domain_id in self._registered_ids(asm):
                try:
                    await asm.stop_domain(domain_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[evolve] stop {domain_id} 失败: {e}")
            asm.unregister(domain_id)
        if forget:
            try:
                from LLM_v2.domains.capability_ops.capability_ops_impl import (
                    forget_capability,
                )
                forget_capability(domain_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[evolve] 清理准入记录失败: {e}")
        for key in list(sys.modules):
            if key.startswith(f"LLM_v2.domains.{domain_id}"):
                sys.modules.pop(key, None)
        if delete_source:
            shutil.rmtree(_DOMAINS_ROOT / domain_id, ignore_errors=True)
        return True

    # ---------- 查询 / 运行时 ----------

    @staticmethod
    def _assembly():
        try:
            from LLM_v2.core.persona_runtime import get_runtime
            return getattr(get_runtime(), "assembly", None)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _registered_ids(asm) -> set:
        if asm is None:
            return set()
        try:
            return {w.get("id") for w in asm.worlds_status().get("worlds", [])}
        except Exception:  # noqa: BLE001
            return set()

    @staticmethod
    def _list_evolutions() -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if not _DOMAINS_ROOT.is_dir():
            return items
        for d in sorted(_DOMAINS_ROOT.glob("evo_*")):
            if not d.is_dir():
                continue
            try:
                manifest = json.loads(
                    (d / "manifest.json").read_text(encoding="utf-8"))
                tool = json.loads(
                    (d / "tool.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not manifest.get("evolved"):
                continue
            risk = str(manifest.get("risk") or "R2").strip().upper()
            if risk not in ("R0", "R1", "R2", "R3"):
                risk = "R2"
            items.append({
                "name": manifest.get("id"),
                "domain_id": manifest.get("id"),
                "label": manifest.get("label"),
                "tool_name": tool.get("name") or manifest.get("tool"),
                "description": manifest.get("description") or "",
                "rev": manifest.get("rev") or 1,
                "risk": risk,
                "file": str(d / "handler.py"),
                "created_at": manifest.get("created_at"),
            })
        return items


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = raw[start:end + 1]
    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            obj = json.loads(attempt)
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            continue
    return None


def build_evolve_domain() -> EvolveDomain:
    return EvolveDomain()


__all__ = ["EvolveDomain", "build_evolve_domain"]
