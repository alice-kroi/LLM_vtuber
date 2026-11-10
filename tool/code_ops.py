# ============================================================
# tool/code_ops.py — 代码工程操作工具集（coder 域实现）
# ============================================================
# 与 tool/computer_use.py 并列：computer_use 管"桌面"，本文件管"代码工程"。
# 统一返回 {success: bool, error?: str, ...}（与 computer_use 约定一致）。
# 全部阻塞 IO 走线程池，避免阻塞直播主进程。
#
# 设计文档：docs/AI_CODING_CAPABILITY.md §4.1 / §7
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
DEFAULT_WORKSPACE = os.getenv("CODE_WORKSPACE") or _PROJECT_ROOT

# 目录树/检索时忽略的目录与后缀
_IGNORE_DIRS: Set[str] = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", "dist", "build", "out",
    ".idea", ".vscode", ".cache", "logs", "audio", "models",
    "GPT-SoVITS-v2pro", "face_dataset", "screenshots",
}
_IGNORE_SUFFIX: Set[str] = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".pkl", ".pt", ".pth",
    ".onnx", ".db", ".sqlite", ".zip", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".mp3", ".wav", ".mp4", ".avi", ".mkv", ".flac",
}

_MAX_SOURCE_BYTES = 2 * 1024 * 1024      # 单文件检索上限 2MB
_MAX_OUTPUT = 60 * 1024                  # 命令输出截断 60KB
_MAX_LINE_COUNT_BYTES = 512 * 1024        # 统计行数的文件上限

# 危险命令黑名单（硬拦截，不可被 LLM 说服）
_DANGEROUS: List[Tuple[str, str]] = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "rm -rf"),
    (r"\bdel\s+/[fsq]\b", "del /f /s /q"),
    (r"\bformat\s+[a-zA-Z]:", "format 磁盘"),
    (r"\bdiskpart\b", "diskpart"),
    (r"\bshutdown\b", "shutdown"),
    (r"\breg\s+delete\b", "reg delete"),
    (r"\btakeown\b", "takeown"),
    (r"\bicacls\b.*\/grant", "icacls 改权限"),
    (r"\bbcdedit\b", "bcdedit"),
    (r"git\s+push\s+.*--force", "git push --force"),
    (r"git\s+push\s+.*-f\b", "git push -f"),
    (r"git\s+reset\s+--hard", "git reset --hard"),
    (r"git\s+clean\s+-[a-z]*f", "git clean -f"),
    (r"curl[^|]*\|\s*(?:ba)?sh", "curl | bash"),
    (r"wget[^|]*\|\s*(?:ba)?sh", "wget | bash"),
    (r"\bsubprocess\b.*SESSDATA", "疑似读取凭据"),
]


# ---------- 基础工具 ----------

async def _in_thread(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def resolve_workspace(workspace: Optional[str] = None) -> Path:
    """解析工作区根（白名单边界）。"""
    raw = workspace or DEFAULT_WORKSPACE
    p = Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()
    return p


def _ensure_inside(path: Optional[str], workspace: Path) -> Path:
    """把 path 约束在 workspace 内；越界抛 PermissionError。"""
    if not path:
        return workspace
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    if not p.is_absolute():
        p = workspace / p
    p = p.resolve()
    try:
        p.relative_to(workspace)
    except ValueError:
        raise PermissionError(f"越出工作区: {p} 不在 {workspace} 内")
    return p


def check_command(cmd: str) -> Optional[str]:
    """危险命令检查。返回 None=安全，否则返回拒绝理由。"""
    low = (cmd or "").lower()
    for pattern, label in _DANGEROUS:
        if re.search(pattern, low):
            return f"命令被安全策略拦截：{label}"
    return None


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> Tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) > limit:
        return text[:limit], True
    return text, False


def _count_lines(p: Path) -> Optional[int]:
    """轻量统计行数（大文件返回 None，避免卡顿）。"""
    try:
        if p.stat().st_size > _MAX_LINE_COUNT_BYTES:
            return None
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return None


def _iter_source_files(base: Path):
    """遍历源码文件（跳过忽略目录/二进制后缀）。"""
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            p = Path(root) / name
            if p.suffix.lower() in _IGNORE_SUFFIX:
                continue
            yield p


# ---------- 1. code_tree ----------

def _code_tree_sync(root: Optional[str], workspace: Optional[str],
                    depth: int, max_entries: int) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        base = _ensure_inside(root or ".", ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    if not base.exists():
        return {"success": False, "error": f"路径不存在: {base}"}

    lines: List[str] = []
    count = 0
    truncated = False

    def walk(d: Path, level: int):
        nonlocal count, truncated
        if truncated or level > depth:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for e in entries:
            if count >= max_entries:
                truncated = True
                return
            if e.name in _IGNORE_DIRS or e.name.startswith(".") or e.suffix.lower() in _IGNORE_SUFFIX:
                continue
            try:
                rel = e.relative_to(ws).as_posix()
            except ValueError:
                rel = e.name
            if e.is_dir():
                lines.append("  " * level + f"[{e.name}/]  ({rel})")
                walk(e, level + 1)
            else:
                n = _count_lines(e)
                size = f"{n}L" if n is not None else "?"
                lines.append("  " * level + f"{e.name}  ({size})")
            count += 1

    walk(base, 0)
    return {
        "success": True,
        "root": str(base),
        "workspace": str(ws),
        "entries": count,
        "truncated": truncated,
        "tree": "\n".join(lines) if lines else "(空目录)",
    }


async def code_tree(root: str = ".", workspace: Optional[str] = None,
                    depth: int = 3, max_entries: int = 200) -> Dict[str, Any]:
    """[代码] 输出目录树（忽略 .git/__pycache__/node_modules 等）。"""
    return await _in_thread(_code_tree_sync, root, workspace, int(depth), int(max_entries))


# ---------- 2. code_search ----------

def _code_search_sync(pattern: str, root: Optional[str], workspace: Optional[str],
                      glob: str, context_lines: int, max_hits: int,
                      case_sensitive: bool) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        base = _ensure_inside(root or ".", ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    if not base.exists():
        return {"success": False, "error": f"路径不存在: {base}"}
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern, flags)
    except re.error as e:
        return {"success": False, "error": f"正则无效: {e}"}

    try:
        import fnmatch
        target = base.rglob("*") if glob in ("", "*", None) else (
            p for p in base.rglob("*") if fnmatch.fnmatch(p.name, glob))
    except Exception as e:
        return {"success": False, "error": f"遍历失败: {e}"}

    hits: List[Dict[str, Any]] = []
    scanned = 0
    for p in target:
        if len(hits) >= max_hits:
            break
        try:
            if not p.is_file() or p.suffix.lower() in _IGNORE_SUFFIX:
                continue
            if any(part in _IGNORE_DIRS or part.startswith(".") for part in p.parts):
                continue
            if p.stat().st_size > _MAX_SOURCE_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned += 1
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if len(hits) >= max_hits:
                break
            if rx.search(line):
                lo = max(0, i - context_lines)
                hi = min(len(lines), i + context_lines + 1)
                try:
                    rel = p.relative_to(ws).as_posix()
                except ValueError:
                    rel = str(p)
                hits.append({
                    "file": rel,
                    "line": i + 1,
                    "text": line.strip()[:300],
                    "context": "\n".join(lines[lo:hi])[:800],
                })
    return {
        "success": True,
        "pattern": pattern,
        "hits": hits,
        "hit_count": len(hits),
        "scanned_files": scanned,
        "truncated": len(hits) >= max_hits,
    }


async def code_search(pattern: str, root: str = ".", workspace: Optional[str] = None,
                      glob: str = "*", context_lines: int = 2, max_hits: int = 50,
                      case_sensitive: bool = False) -> Dict[str, Any]:
    """[代码] 按内容检索（正则），等价 ripgrep，用于定位要改的代码。"""
    if not pattern:
        return {"success": False, "error": "pattern 不能为空"}
    return await _in_thread(_code_search_sync, pattern, root, workspace, glob,
                            int(context_lines), int(max_hits), bool(case_sensitive))


# ---------- 3. code_run_tests ----------

def _run_shell_sync(cmd: str, cwd: Optional[str], timeout: float) -> Dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    t0 = time.monotonic()
    try:
        cp = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"命令超时 ({timeout}s)", "timeout": True,
                "duration_s": round(time.monotonic() - t0, 2)}
    except Exception as e:
        return {"success": False, "error": f"命令执行失败: {e}"}
    out, out_tr = _truncate(cp.stdout or "")
    err, err_tr = _truncate(cp.stderr or "")
    return {
        "success": cp.returncode == 0,
        "returncode": cp.returncode,
        "stdout": out,
        "stderr": err,
        "truncated": bool(out_tr or err_tr),
        "duration_s": round(time.monotonic() - t0, 2),
    }


def _parse_pytest(out: str, err: str) -> Dict[str, Any]:
    """把 pytest 输出解析成结构化判定信号。"""
    blob = f"{out}\n{err}"
    def _num(p):
        m = re.search(p, blob)
        return int(m.group(1)) if m else 0

    passed = _num(r"(\d+)\s+passed")
    failed = _num(r"(\d+)\s+failed")
    errors = _num(r"(\d+)\s+error")
    skipped = _num(r"(\d+)\s+skipped")
    no_tests = bool(re.search(r"no tests ran|collected 0 items", blob))

    first = None
    m = re.search(r"^FAILED\s+(\S+?)(?:\s+-\s+(.*))?$", blob, re.MULTILINE)
    if m:
        loc = m.group(1)
        msg = (m.group(2) or "").strip()
        file_part, _, name = loc.partition("::")
        line_no = None
        lm = re.search(re.escape(file_part) + r":(\d+):", blob)
        if lm:
            line_no = int(lm.group(1))
        if not msg:
            em = re.search(r"^E\s+(.+)$", blob, re.MULTILINE)
            msg = (em.group(1).strip() if em else "")[:300]
        first = {"file": file_part, "test": name, "line": line_no, "message": msg[:300]}

    return {
        "passed": passed, "failed": failed, "errors": errors, "skipped": skipped,
        "no_tests": no_tests, "first_failure": first,
    }


def _code_run_tests_sync(cmd: str, cwd: Optional[str], workspace: Optional[str],
                         timeout: float) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        run_cwd = str(_ensure_inside(cwd or ".", ws)) if cwd else str(ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    blocked = check_command(cmd)
    if blocked:
        return {"success": False, "error": blocked, "blocked": True}

    raw = _run_shell_sync(cmd, run_cwd, timeout)
    if raw.get("error"):
        return raw
    parsed = _parse_pytest(raw.get("stdout", ""), raw.get("stderr", ""))
    ok = (raw.get("returncode") == 0) and parsed["failed"] == 0 and parsed["errors"] == 0
    return {
        "success": ok,
        "cmd": cmd,
        "cwd": run_cwd,
        **parsed,
        "returncode": raw.get("returncode"),
        "duration_s": raw.get("duration_s"),
        "stdout": raw.get("stdout", ""),
        "stderr": raw.get("stderr", ""),
        "truncated": raw.get("truncated", False),
    }


async def code_run_tests(cmd: str, cwd: Optional[str] = None,
                         workspace: Optional[str] = None,
                         timeout: float = 300.0) -> Dict[str, Any]:
    """[代码] 运行测试并解析结果（结构化 passed/failed/first_failure）。"""
    if not cmd:
        return {"success": False, "error": "cmd 不能为空"}
    return await _in_thread(_code_run_tests_sync, cmd, cwd, workspace, float(timeout))


# ---------- 4~6. Git ----------

def _git(cwd: Path, args: List[str], timeout: float = 60.0) -> Dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("GIT_PAGER", "cat")
    try:
        cp = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, env=env, encoding="utf-8", errors="replace",
        )
    except Exception as e:
        return {"success": False, "error": f"git 执行失败: {e}"}
    out, tr = _truncate(cp.stdout or "")
    err, _ = _truncate(cp.stderr or "")
    return {
        "success": cp.returncode == 0,
        "returncode": cp.returncode,
        "stdout": out,
        "stderr": err,
        "truncated": tr,
    }


def _code_git_status_sync(cwd: Optional[str], workspace: Optional[str]) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        base = _ensure_inside(cwd or ".", ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    r = _git(base, ["status", "--porcelain", "-b"])
    if not r.get("success"):
        return r
    lines = [ln for ln in r["stdout"].splitlines() if ln.strip()]
    branch = ""
    changed: List[str] = []
    untracked: List[str] = []
    for ln in lines:
        if ln.startswith("##"):
            branch = ln[2:].strip()
            continue
        code, _, path = ln[:2], ln[2:3], ln[3:].strip()
        (untracked if code.strip() == "??" else changed).append(path)
    return {
        "success": True, "cwd": str(base), "branch": branch,
        "changed": changed, "untracked": untracked, "clean": not changed and not untracked,
    }


def _code_git_diff_sync(cwd: Optional[str], workspace: Optional[str],
                        stat: bool) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        base = _ensure_inside(cwd or ".", ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    r = _git(base, ["diff"] + (["--stat"] if stat else []))
    if not r.get("success"):
        return r
    return {"success": True, "cwd": str(base), "stat": stat,
            "diff": r["stdout"], "empty": not r["stdout"].strip()}


def _code_git_restore_sync(cwd: Optional[str], workspace: Optional[str],
                           paths: Optional[List[str]], all_: bool) -> Dict[str, Any]:
    ws = resolve_workspace(workspace)
    try:
        base = _ensure_inside(cwd or ".", ws)
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    if not all_ and not paths:
        return {"success": False, "error": "需提供 paths 或 all=true"}
    if all_:
        r = _git(base, ["checkout", "--", "."])
        return {"success": r.get("success", False), "restored": "all",
                "stdout": r.get("stdout", ""), "stderr": r.get("stderr", ""),
                "error": r.get("error")}
    safe: List[str] = []
    for p in paths:
        try:
            rel = _ensure_inside(p, base).relative_to(base).as_posix()
            safe.append(rel)
        except (PermissionError, ValueError):
            return {"success": False, "error": f"路径越界: {p}"}
    r = _git(base, ["checkout", "--", *safe])
    return {"success": r.get("success", False), "restored": safe,
            "stderr": r.get("stderr", ""), "error": r.get("error")}


async def code_git_status(cwd: Optional[str] = None,
                          workspace: Optional[str] = None) -> Dict[str, Any]:
    """[代码] git 工作区状态（动手前先确认是否干净）。"""
    return await _in_thread(_code_git_status_sync, cwd, workspace)


async def code_git_diff(cwd: Optional[str] = None, workspace: Optional[str] = None,
                        stat: bool = False) -> Dict[str, Any]:
    """[代码] git diff（看改了什么 / 给播报用）。"""
    return await _in_thread(_code_git_diff_sync, cwd, workspace, bool(stat))


async def code_git_restore(cwd: Optional[str] = None, workspace: Optional[str] = None,
                           paths: Optional[List[str]] = None,
                           all: bool = False) -> Dict[str, Any]:
    """[代码] 回滚改动（改坏了用）。"""
    return await _in_thread(_code_git_restore_sync, cwd, workspace, paths, bool(all))


__all__ = [
    "DEFAULT_WORKSPACE",
    "resolve_workspace",
    "check_command",
    "code_tree", "code_search", "code_run_tests",
    "code_git_status", "code_git_diff", "code_git_restore",
]
