# ============================================================
# LLM_v2/brains/coding_loop.py — 操作脑的编码循环（有界 ReAct）
# ============================================================
# 职责：在意志脑给定目标内，自主完成「侦察 → 计划 → 修改 → 验证 → 修正」。
# 边界（三脑分工）：
#   - 只做"how"：选工具、读报错、重试；不产出对观众的话、不做价值观判断。
#   - 不 emit 任何播报事件；进度通过 on_progress 回调交回 OperatorBrain。
#   - 不越权：所有路径受工作区白名单约束，危险命令由 tool/code_ops.py 拦截。
#
# 硬规则（设计文档 §5.4）：
#   done = 最近一次编辑之后，有过一次成功的验证（测试/脚本执行）
#   不接受"模型自称已修复"；未验证的改动一律不算完成。
#
# 查资料：遇到不熟悉的 API/库可调 research 域（wr_search/wr_read/wr_notes/
#   wr_note_save），但研究调用有上限（MAX_RESEARCH_CALLS），查到关键点必须
#   回到编辑与验证；研究步骤也会进入程序性记忆，下次同类任务可复用。
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# WebUI 事件日志（实时可见；导入失败不阻塞）
try:
    from LLM_v2.io.webui import log_event as _log_event
except Exception:  # noqa: BLE001
    def _log_event(level, source, message):  # type: ignore
        pass

# 循环内允许暴露给 LLM 的工具（白名单，避免把 B 站/启动器等无关工具塞进来）
ALLOWED_TOOL_NAMES = {
    # coder 域
    "code_tree", "code_search", "code_run_tests",
    "code_git_status", "code_git_diff", "code_git_restore",
    # desktop 域中的文件/命令子集
    "os_read_file", "os_create_file", "os_edit_file",
    "os_run_command", "os_list_directory", "os_mkdir",
    # research 域：编码时现场查资料
    "wr_search", "wr_read", "wr_note_save", "wr_notes",
}

RESEARCH_TOOLS = {"wr_search", "wr_read", "wr_note_save", "wr_notes"}
# 研究结果回灌的字符上限：读页比搜索长，但仍要有界
RESEARCH_BRIEF_LIMITS = {
    "wr_search": 1800,
    "wr_read": 3000,
    "wr_notes": 1500,
    "wr_note_save": 600,
}
MAX_RESEARCH_CALLS = 4      # 单次编码会话最多查资料次数，防止把预算耗在浏览上

EDIT_TOOLS = {"os_create_file", "os_edit_file"}
VERIFY_TOOLS = {"code_run_tests", "os_run_command"}
# 这些工具需要注入 workspace 参数（沙箱边界）
_WORKSPACE_TOOLS = {"code_tree", "code_search", "code_run_tests",
                    "code_git_status", "code_git_diff", "code_git_restore"}

DEFAULT_MAX_ROUNDS = int((__import__("os").getenv("CODE_MAX_ROUNDS") or 14))
DEFAULT_NO_PROGRESS = 2
DEFAULT_MAX_TOOL_CALLS = 40
DEFAULT_TOTAL_TIMEOUT = 1200.0     # 20 分钟
# 侦察预算：超过这么多轮还没动手写文件，就强制进入编辑阶段
RECON_ROUNDS = int((__import__("os").getenv("CODE_RECON_ROUNDS") or 3))

# 历史裁剪：每轮都会累积 user/assistant/tool 消息，长会话要限量（字符数）
DEFAULT_HISTORY_CHARS = int((__import__("os").getenv("CODE_MAX_HISTORY_CHARS") or 60000))

# 模型把整个长文件塞进一次工具调用时，参数 JSON 会被 max_tokens 截断。
# 这类错误可恢复：改成分段写入，而不是让整次编码会话中止。
_PAYLOAD_TRUNCATION_MARKERS = (
    "unterminated string", "expecting value", "expecting ',' delimiter",
    "jsondecode", "invalid json", "truncat", "length limit",
)
SPLIT_WRITE_HINT = (
    "上一次调用失败/参数被截断，文件内容太长。请改成小步写入："
    "先用 os_create_file 只写文件骨架（不超过 80 行），"
    "再用 os_edit_file 每次补充不超过 60 行，直到写完；不要一次提交整个长文件。"
)


def _is_payload_truncation(err: str) -> bool:
    low = str(err or "").lower()
    return any(m in low for m in _PAYLOAD_TRUNCATION_MARKERS)


SYSTEM_TEMPLATE = """你是「维尔薇」操作脑里的**编程执行器**。你只负责把给定目标在代码层面做成，不产出对观众说的话。

【目标】
{goal}

【验收标准】
{acceptance}

【工作区（白名单）】
{workspace}
所有路径必须在该目录内，越界会被拒绝。

【铁律】
1. 先侦察再动手：code_git_status → code_tree → code_search → 读关键文件。
   **遇到不熟悉的 API / 库 / 报错，先 wr_search 查，再 wr_read 读 1 个官方文档页。**
   研究和侦察合计最多 {recon_rounds} 轮；查到关键点就回到代码，不要一直浏览。
2. 改文件用 os_edit_file（Search-Replace，search_text 必须与文件内容逐字一致）；
   新建文件用 os_create_file（overwrite=true 可覆盖）。
   **单次写入不超过 80 行**：长文件先写骨架，再用 os_edit_file 分块补齐，
   一次塞进整个长文件会因输出上限被截断而失败。
3. **改完必须验证**：优先 code_run_tests；没有测试就 os_run_command 跑脚本/编译。
4. 验证失败时，把错误里的**文件+行号+消息**当作下一步依据，不要盲目重写整个文件。
5. **未运行验证就声称完成是禁止的**，系统会判定该轮无效。
6. 危险命令（rm -rf / git push --force / format / diskpart 等）会被拦截，别尝试。
7. 不要一次改太多；小步改、快速验证。
8. 查到可复用的结论时，用 wr_note_save 存一条笔记（topic / summary / sources），
   下次同类任务可先用 wr_notes 召回；不要只查不记。

【新建能力域时的硬性契约】（目标里出现 LLM_v2/domains/<id>/ 时必须遵守）
不能自创结构，必须与现有能力域一致，否则会被准入闸门拒绝：
- `<id>.py`：`class <Id>Domain(Actuator)`，类属性 `id/label/prefix/default_enabled/type = CapabilityType.ACTUATOR`；
  实现 `allowed_names() -> List[str]` 与 `tools() -> List[Dict[str, Any]]`；
  工厂 `build_<id>_domain()` **必须 return 域实例**（返回 dict 会被判失败）。
- `<id>_tools.py`：`TOOL_NAMES` + `TOOLS_SCHEMA`，用 `LLM_v2.core.capability_domain.tool_schema` 构造。
- `__init__.py`：`from .<id> import <Id>Domain, build_<id>_domain`。
- `manifest.json`：字段 `id / label / version / risk / smoke`；smoke 是「工具名 → 参数对象」的映射。
- 工具函数**必须能在域实例上直接取到**：写成域类的方法，或在 `__init__` 里用 `setattr` 绑定；
  只放在 `_tools.py` 里的模块级函数不会被调度，冒烟测试会报 `has no attribute`。
- 工具函数**必须返回 dict 且含 `success` 字段**；`success: false` 视为失败。
- 现成范本可直接读：`LLM_v2/domains/hf_hub/`（联网）、`LLM_v2/domains/capability_ops/`（本地）。

【输出要求】
- 需要动手时：调用工具（一次可调一个，必要时可多个）。
- 已通过验证且目标达成时：不再调用工具，用一两句话说明"改了什么、验证结果如何"。
"""


# ---------- 会话持久化（与 persona/state.db 同库） ----------

class CodingStore:
    """编程会话落盘（沿用 StateDB 的 sqlite 模式，独立连接 + 锁）"""

    def __init__(self, db_path: str = "persona/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS coding_sessions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at    TEXT NOT NULL,
                    finished_at   TEXT,
                    goal          TEXT NOT NULL,
                    acceptance    TEXT,
                    workspace     TEXT,
                    status        TEXT,
                    rounds        INTEGER DEFAULT 0,
                    tool_calls    INTEGER DEFAULT 0,
                    model         TEXT,
                    files_changed TEXT,
                    final_error   TEXT,
                    git_diff_stat TEXT
                );
                CREATE TABLE IF NOT EXISTS coding_steps (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  INTEGER NOT NULL,
                    round_no    INTEGER NOT NULL,
                    phase       TEXT,
                    tool        TEXT,
                    args_digest TEXT,
                    success     INTEGER,
                    detail      TEXT,
                    at          TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_coding_steps_sess
                    ON coding_steps(session_id, round_no);
            """)

    def start_session(self, goal: str, acceptance: str, workspace: str, model: str) -> int:
        try:
            with self._lock, self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO coding_sessions(started_at, goal, acceptance, workspace, status, model)"
                    " VALUES (?,?,?,?,?,?)",
                    (time.strftime("%Y-%m-%d %H:%M:%S"), goal, acceptance, workspace, "running", model),
                )
                return int(cur.lastrowid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[coding] 会话落盘失败（不影响执行）: {e}")
            return 0

    def record_step(self, session_id: int, round_no: int, phase: str, tool: str,
                    args: Dict[str, Any], success: bool, detail: str) -> None:
        if not session_id:
            return
        try:
            digest = json.dumps(args, ensure_ascii=False)[:400]
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO coding_steps(session_id, round_no, phase, tool, args_digest,"
                    " success, detail, at) VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, round_no, phase, tool, digest, 1 if success else 0,
                     (detail or "")[:500], time.strftime("%Y-%m-%d %H:%M:%S")),
                )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] step 落盘失败: {e}")

    def finish_session(self, session_id: int, status: str, rounds: int, tool_calls: int,
                       files_changed: List[str], final_error: str, diff_stat: str) -> None:
        if not session_id:
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE coding_sessions SET finished_at=?, status=?, rounds=?, tool_calls=?,"
                    " files_changed=?, final_error=?, git_diff_stat=? WHERE id=?",
                    (time.strftime("%Y-%m-%d %H:%M:%S"), status, rounds, tool_calls,
                     json.dumps(files_changed, ensure_ascii=False), (final_error or "")[:1000],
                     (diff_stat or "")[:1000], session_id),
                )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 会话收尾失败: {e}")

    def load_session(self, session_id: int) -> Dict[str, Any]:
        """读取一次会话（程序性记忆抽取用）"""
        if not session_id:
            return {}
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM coding_sessions WHERE id=?", (session_id,)
                ).fetchone()
                return dict(row) if row else {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 读取会话失败: {e}")
            return {}

    def load_steps(self, session_id: int) -> List[Dict[str, Any]]:
        """读取一次会话的全部步骤（按发生顺序）"""
        if not session_id:
            return []
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM coding_steps WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 读取步骤失败: {e}")
            return []


# ---------- 循环本体 ----------

class CodingLoop:
    """有界 ReAct 编码循环"""

    def __init__(
        self,
        runtime,
        llm=None,
        workspace: Optional[str] = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        no_progress_rounds: int = DEFAULT_NO_PROGRESS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
        llm_timeout: float = 60.0,
        spec_id: Optional[str] = None,
    ):
        self.runtime = runtime
        self._llm = llm
        self.workspace = workspace
        self.max_rounds = max_rounds
        self.no_progress_rounds = no_progress_rounds
        self.max_tool_calls = max_tool_calls
        self.total_timeout = total_timeout
        self.llm_timeout = llm_timeout
        # 规格驱动：有已审批规格时，需求账本每轮注入、改文件后转 stale、
        # 收尾按 check 命令取证，没证据不算完成
        self.spec_id = spec_id
        self._spec_store_obj = None

    # ---------- 依赖 ----------

    def _get_llm(self):
        if self._llm is None:
            from LLM_v2.core.llm_adapter import get_coding_llm
            self._llm = get_coding_llm()
        return self._llm

    def _learning_store(self):
        """学习库：优先复用人格内核的实例（同一个 state.db）"""
        core = getattr(self.runtime, "self_core", None)
        store = getattr(core, "learning", None)
        if store is not None:
            return store
        try:
            from persona.learning.store import LearningStore
            return LearningStore()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 学习库不可用: {e}")
            return None

    def _state_values(self):
        """当前资源条，用于 E/S/C 分档签名"""
        state = getattr(self.runtime, "state", None)
        return (getattr(state, "energy", 60),
                getattr(state, "social_need", 50),
                getattr(state, "curiosity", 50))

    # ---------- 规格账本 ----------

    def _spec_store(self):
        """规格账本（与学习库同库，失败不影响编码本身）"""
        if self._spec_store_obj is not None:
            return self._spec_store_obj
        try:
            from LLM_v2.domains.spec_workflow.store import SpecStore
            learning = self._learning_store()
            db = str(learning.db_path) if learning is not None else "persona/state.db"
            self._spec_store_obj = SpecStore(db)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 规格账本不可用: {e}")
            return None
        return self._spec_store_obj

    def _load_spec(self):
        """取本次要遵守的规格：显式 spec_id 优先，否则取当前活跃规格"""
        store = self._spec_store()
        if store is None:
            return None, ""
        try:
            spec = store.find(self.spec_id) if self.spec_id else store.active()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[coding] 规格读取失败: {e}")
            return None, ""
        if not spec or not spec.get("requirements"):
            return None, ""
        return spec, store.requirements_block(spec)

    @staticmethod
    def _cmd_matches(req_cmd: str, run_cmd: str) -> bool:
        """check 命令与刚才执行的命令是否同一件事（宽松包含匹配）"""
        a = re.sub(r"\s+", " ", (req_cmd or "")).strip().lower()
        b = re.sub(r"\s+", " ", (run_cmd or "")).strip().lower()
        return bool(a) and (a in b or b in a)

    @staticmethod
    def _exit_code(res: Dict[str, Any]) -> Optional[int]:
        """取命令退出码。os_run_command 用 returncode；success 只代表命令跑完了"""
        for key in ("returncode", "exit_code"):
            if res.get(key) is not None:
                try:
                    return int(res[key])
                except (TypeError, ValueError):
                    return None
        return None

    def _record_verify(self, spec, store, tool: str, args: Dict[str, Any],
                       res: Dict[str, Any]) -> None:
        """验证通过 → 给覆盖到的需求记证据"""
        if spec is None or store is None or not res.get("success"):
            return
        rc = self._exit_code(res)
        if rc not in (None, 0):     # 命令跑完但退出码非 0，不算通过
            return
        run_cmd = str(args.get("cmd") or "")
        evidence = {
            "tool": tool,
            "command": run_cmd,
            "exit_code": rc,
            "summary": str(res.get("stdout") or res.get("message") or "")[-300:],
        }
        results = []
        for req in spec.get("requirements") or []:
            if req.get("status") == "verified":
                continue
            check = req.get("check_cmd") or ""
            if check and not self._cmd_matches(check, run_cmd):
                continue
            results.append({"id": req["req_id"], "status": "verified",
                            "evidence": evidence})
        if not results:
            return
        spec = store.record(spec["id"], results)
        logger.info(f"[coding] 需求取证: {[r['id'] for r in results]} "
                    f"via {tool}")
        _log_event("INFO", "coding",
                   f"需求取证：{', '.join(r['id'] for r in results)}（{tool}）")

    async def _audit_requirements(self, spec, store) -> List[Dict[str, Any]]:
        """收尾审计：对没证据的需求跑它的 check 命令，返回仍未通过项"""
        if spec is None or store is None:
            return []
        pending = [r for r in spec.get("requirements") or []
                   if r.get("status") != "verified"]
        if not pending:
            return []
        results = []
        for req in pending[:8]:
            check = req.get("check_cmd") or ""
            if not check:
                continue
            try:
                res = await self._call_tool("os_run_command",
                                            {"cmd": check, "timeout": 120})
            except Exception as e:  # noqa: BLE001
                res = {"success": False, "error": str(e)}
            rc = self._exit_code(res)
            ok = bool(res.get("success")) and rc == 0
            results.append({
                "id": req["req_id"],
                "status": "verified" if ok else "failed",
                "evidence": {
                    "tool": "os_run_command",
                    "command": check,
                    "exit_code": rc,
                    "summary": str(res.get("stdout") or res.get("error") or "")[-300:],
                },
            })
        if results:
            spec = store.record(spec["id"], results)
            _log_event("INFO", "coding",
                       f"需求审计：{sum(1 for r in results if r['status'] == 'verified')}"
                       f"/{len(results)} 通过")
        return [r for r in spec.get("requirements") or []
                if r.get("status") != "verified"]

    @staticmethod
    def _history_chars(history: List[Dict[str, Any]]) -> int:
        """估算历史体积：正文 + 工具调用参数"""
        total = 0
        for msg in history or []:
            total += len(str(msg.get("content") or ""))
            for tc in msg.get("tool_calls") or []:
                total += len(json.dumps(tc.get("function") or {}, ensure_ascii=False))
        return total

    def _trim_history(self, history: List[Dict[str, Any]],
                      max_chars: int = DEFAULT_HISTORY_CHARS
                      ) -> List[Dict[str, Any]]:
        """从最早一轮开始丢，按 role=user 边界切，避免 assistant/tool 配对被打断"""
        if not history or self._history_chars(history) <= max_chars:
            return history
        starts = [i for i, m in enumerate(history) if m.get("role") == "user"]
        if not starts:
            return history[-2:]
        for start in starts[1:]:
            rest = history[start:]
            if self._history_chars(rest) <= max_chars:
                _log_event("INFO", "coding",
                           f"历史裁剪：丢弃最早 {start} 条以控制上下文体积")
                return rest
        return history[starts[-1]:]

    def _learn_procedure(self, coding_store: "CodingStore", session_id: int,
                         recon_budget: int, state_values=None) -> None:
        """会话结束后抽一次流程（规则，零 LLM）"""
        try:
            learning = self._learning_store()
            if learning is None or not session_id:
                return
            from persona.learning.procedure import extract

            session = coding_store.load_session(session_id)
            if not session:
                return
            steps = coding_store.load_steps(session_id)
            cand = extract(session, steps, recon_budget, state_values)
            proc = learning.upsert_procedure(
                cand["signature"], cand["when_to_use"], cand["steps"],
                cand["done_when"], cand["avoid"], evidence=[session_id],
                success=cand["success"])
            logger.info(
                f"[coding] 程序性记忆 {cand['signature']} "
                f"reliability={proc.get('reliability')} status={proc.get('status')} "
                f"steps={len(cand['steps'])} avoid={len(cand['avoid'])}")
            _log_event("INFO", "coding",
                       f"程序性记忆更新：{cand['signature']}｜"
                       f"可靠性 {proc.get('reliability')}｜{proc.get('status')}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[coding] 程序性记忆更新失败（已忽略）: {e}")

    def _tool_schemas(self) -> List[Dict[str, Any]]:
        try:
            all_schemas = self.runtime.assembly.active_tool_schemas()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[coding] 取工具 schema 失败: {e}")
            return []
        picked = [s for s in all_schemas
                  if s.get("function", {}).get("name") in ALLOWED_TOOL_NAMES]
        if not picked:
            logger.warning("[coding] 未取到任何可用工具（coder/desktop 域是否已激活？）")
        return picked

    async def _call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name in _WORKSPACE_TOOLS and "workspace" not in args:
            args = {**args, "workspace": self.workspace}
        try:
            result = await self.runtime.assembly.call_tool(name, args, timeout=320.0)
            if result.get("error"):
                return {"success": False, "error": result["error"]}
            inner = result.get("result")
            return inner if isinstance(inner, dict) else {"success": True, "result": inner}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    # ---------- 辅助 ----------

    @staticmethod
    def _brief(res: Dict[str, Any], limit: int = 900) -> str:
        """把工具结果压成可回灌的简短文本。"""
        try:
            text = json.dumps(res, ensure_ascii=False)
        except Exception:
            text = str(res)
        return text[:limit]

    @staticmethod
    def _error_signature(tool: str, res: Dict[str, Any]) -> str:
        if tool == "code_run_tests" and not res.get("success"):
            ff = res.get("first_failure") or {}
            return f"test:{ff.get('file')}:{ff.get('line')}:{(ff.get('message') or '')[:80]}"
        if not res.get("success"):
            return f"{tool}:{(res.get('error') or '')[:120]}"
        return ""

    @staticmethod
    def _extract_path(tool: str, args: Dict[str, Any]) -> Optional[str]:
        if tool in ("os_create_file", "os_edit_file", "os_read_file"):
            return args.get("path")
        return None

    @staticmethod
    def _wire_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把内部 tool_calls 转成 OpenAI/方舟线上格式（回灌历史时必须用这个）。

        内部格式：{"tool_call_id","name","arguments"}
        线上格式：{"id","type":"function","function":{"name","arguments"(JSON字符串)}}
        """
        out: List[Dict[str, Any]] = []
        for tc in tool_calls or []:
            fn = tc.get("function") or {}
            name = tc.get("name") or fn.get("name") or ""
            args = tc.get("arguments")
            if args is None:
                args = fn.get("arguments")
            if not isinstance(args, str):
                try:
                    args = json.dumps(args or {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
            out.append({
                "id": tc.get("tool_call_id") or tc.get("id") or "",
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
        return out

    @staticmethod
    def _parse_final(text: str) -> bool:
        """粗略判断模型是否在宣称完成。"""
        if not text:
            return False
        keys = ("完成", "已完成", "改好", "已改", "通过", "done", "完成。", "搞定")
        return any(k in text for k in keys)

    # ---------- 主入口 ----------

    async def run(
        self,
        goal: str,
        acceptance: str = "",
        workspace: Optional[str] = None,
        on_progress: Optional[Callable[[int, str, str], Any]] = None,
        model_name: str = "",
    ) -> Dict[str, Any]:
        from tool.code_ops import check_command, resolve_workspace

        ws = resolve_workspace(workspace or self.workspace)
        self.workspace = str(ws)

        llm = self._get_llm()
        if llm is None:
            return {"success": False, "error": "无可用 LLM（Doubao_API_KEY 未配置？）"}

        learning = self._learning_store()
        store = CodingStore(str(learning.db_path) if learning else "persona/state.db")
        session_id = store.start_session(goal, acceptance, str(ws), model_name or getattr(llm, "model", ""))
        logger.info(f"[coding] ===== 会话开始 #{session_id} 模型={getattr(llm, 'model', '?')} 工作区={ws}")
        _log_event("INFO", "coding", f"会话 #{session_id} 开始（模型 {getattr(llm, 'model', '?')}）")
        schemas = self._tool_schemas()
        if not schemas:
            store.finish_session(session_id, "failed", 0, 0, [], "无可用工具", "")
            return {"success": False, "error": "无可用工具（coder/desktop 域未激活）",
                    "session_id": session_id}
        research_ready = any(
            s.get("function", {}).get("name") in RESEARCH_TOOLS for s in schemas)

        # 程序性记忆：同类任务 + 相近 E/S/C 档位上次的路径（召回即计一次 uses）
        procedure = None
        recon_budget = RECON_ROUNDS
        state_values = self._state_values()
        if learning is not None:
            try:
                from persona.learning.procedure import classify, task_label
                label = task_label(classify(goal))
                procedure = learning.recall_procedures(
                    signature_prefix=f"编码|{label}|", state_values=state_values)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[coding] 流程召回失败: {e}")
        if procedure:
            recon_budget = min(RECON_ROUNDS, 2)   # 有经验就不再多花侦察轮次

        # 规格账本：有已审批规格就按需求条目执行，没证据不算完成
        spec_store = self._spec_store()
        spec, ledger = self._load_spec()
        if spec:
            logger.info(f"[coding] 绑定规格 {spec['id']}（{spec['title']}）"
                        f"需求 {len(spec.get('requirements') or [])} 条")
            _log_event("INFO", "coding",
                       f"绑定规格：{spec['title']}｜需求 "
                       f"{len(spec.get('requirements') or [])} 条")

        system_prompt = SYSTEM_TEMPLATE.format(
            goal=goal, acceptance=acceptance or "（未指定，以'能跑通'为准）",
            workspace=ws, recon_rounds=recon_budget)
        if ledger:
            system_prompt += (
                "\n\n【规格驱动交付铁律】\n" + ledger +
                "\n每轮都必须对照这份清单；已通过项不得回落；"
                "改过文件后 [x] 会变成 [~]，必须重新验证。"
                "没跑过验证命令的需求一律不算完成。")
        if not research_ready:
            system_prompt += (
                "\n\n【注意】research 域未启用，本次不能联网查资料；"
                "遇到不确定的 API 先读本地代码和已有文档，不要臆造。")
        if procedure:
            from persona.learning.procedure import render_block
            system_prompt += "\n\n" + render_block(procedure)
            logger.info(
                f"[coding] 召回流程 {procedure['signature']} "
                f"reliability={procedure.get('reliability')} "
                f"uses={procedure.get('uses')} 当前档位={state_values} "
                f"→ recon_budget={recon_budget}")
            _log_event("INFO", "coding",
                       f"召回程序性记忆：{procedure['signature']}"
                       f"（可靠性 {procedure.get('reliability')}，侦察预算 {recon_budget} 轮）")

        history: List[Dict[str, Any]] = []
        user_text = ("开始。先侦察项目，再制定文件级计划，然后小步修改并验证。"
                     f"目标：{goal}")
        started = time.monotonic()
        tool_calls_total = 0
        files_touched: List[str] = []
        dirty = False          # 上次验证之后是否又改过文件
        verified = False
        last_verify: Optional[Dict[str, Any]] = None
        last_sig = ""
        same_sig_rounds = 0
        nudge_count = 0
        edit_started = False   # 是否已经真正动过手（写过文件）
        research_calls = 0     # 本会话已查资料次数（有上限，防止只查不写）
        payload_retries = 0    # 工具参数被截断后的重试次数（上限 2）
        abort_reason = ""
        round_no = 0

        async def progress(pct: int, stage: str, detail: str):
            if on_progress is None:
                return
            try:
                r = on_progress(pct, stage, detail)
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[coding] 进度回调失败: {e}")

        for round_no in range(1, self.max_rounds + 1):
            # 预算检查
            if tool_calls_total >= self.max_tool_calls:
                abort_reason = f"工具调用超限({self.max_tool_calls})"
                break
            if time.monotonic() - started > self.total_timeout:
                abort_reason = f"总时长超限({int(self.total_timeout)}s)"
                break

            await progress(int((round_no - 1) / self.max_rounds * 100), "thinking",
                           f"第 {round_no}/{self.max_rounds} 轮：思考下一步")

            # 每轮重贴需求账本（上下文会被压缩/遗忘，账本不能只出现一次）
            round_text = (ledger + "\n\n" + user_text) if ledger else user_text

            try:
                result = await asyncio.wait_for(
                    llm.chat(system_prompt=system_prompt, user_text=round_text,
                             history=self._trim_history(history), tools=schemas),
                    timeout=self.llm_timeout,
                )
            except asyncio.TimeoutError:
                abort_reason = "LLM 超时"
                break
            except Exception as e:  # noqa: BLE001
                abort_reason = f"LLM 异常: {e}"
                break

            if result.get("error"):
                err_text = str(result["error"])
                if _is_payload_truncation(err_text) and payload_retries < 2:
                    payload_retries += 1
                    await progress(int((round_no - 1) / self.max_rounds * 100),
                                   "guard", "输出被截断，要求分段写入")
                    _log_event("WARN", "coding",
                               f"r{round_no} 参数截断，转小步写入（{payload_retries}/2）")
                    history.append({"role": "user", "content": round_text})
                    history.append({
                        "role": "assistant",
                        "content": f"(本轮调用失败：{err_text[:120]})",
                    })
                    user_text = SPLIT_WRITE_HINT
                    continue
                abort_reason = f"LLM 错误: {err_text}"
                break

            response_text = (result.get("response") or "").strip()
            tool_calls = result.get("tool_calls") or []
            logger.info(
                f"[coding] r{round_no} llm: tool_calls={len(tool_calls)} "
                f"text={response_text[:120]!r}"
            )
            _log_event("INFO", "coding",
                       f"r{round_no} 模型返回：{len(tool_calls)} 个工具调用"
                       + (f"｜{response_text[:60]}" if response_text else ""))

            # ---- 无工具调用：判定是否真的完成 ----
            if not tool_calls:
                if verified and not dirty:
                    await progress(100, "done", "验证通过，目标达成")
                    history.append({"role": "user", "content": round_text})
                    history.append({"role": "assistant", "content": response_text})
                    break
                nudge_count += 1
                if nudge_count > 2:
                    abort_reason = "模型反复声称完成但未提供可验证结果"
                    break
                # 抗幻觉：未验证就宣称完成 → 判该轮无效，强制继续
                await progress(int((round_no - 1) / self.max_rounds * 100), "guard",
                               "未通过验证，拒绝判定完成")
                history.append({"role": "user", "content": round_text})
                history.append({"role": "assistant", "content": response_text})
                user_text = ("你还没有运行任何验证。**未验证的改动不算完成。**"
                             + ("需求清单里每一条都要有验证证据；" if ledger else "")
                             + "请立刻调用 code_run_tests（或 os_run_command 跑脚本/编译）验证；"
                             "如果还没改文件，请先动手改。")
                continue

            # ---- 执行工具调用 ----
            history.append({"role": "user", "content": round_text})
            history.append({
                "role": "assistant",
                "content": response_text,
                # 回灌必须用线上格式，否则方舟报 MissingParameter: messages.tool_calls.function
                "tool_calls": self._wire_tool_calls(tool_calls),
            })

            round_errors: List[str] = []
            need_split = False
            for tc in tool_calls:
                if tool_calls_total >= self.max_tool_calls:
                    break
                name = tc.get("name") or tc.get("function") or ""
                args = tc.get("arguments") or tc.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if not isinstance(args, dict):
                    args = {}

                # 参数 JSON 被截断：不执行（空参数会写坏文件），改为要求分段写入
                arg_err = str(tc.get("arguments_error") or "")
                if arg_err:
                    need_split = True
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.get("tool_call_id", ""),
                        "content": self._brief({
                            "success": False,
                            "error": f"参数 JSON 被截断（{arg_err[:80]}），本次调用未执行。"
                                     "请改成小步写入：os_create_file 只写骨架（≤80 行），"
                                     "再用 os_edit_file 每次补 ≤60 行。",
                        }, 400),
                    })
                    store.record_step(session_id, round_no, "blocked", name or "?",
                                      {}, False, f"参数截断: {arg_err[:120]}")
                    continue

                # 命令安全拦截（os_run_command 也在保护范围内）
                if name in ("os_run_command", "code_run_tests"):
                    blocked = check_command(str(args.get("cmd") or ""))
                    if blocked:
                        res = {"success": False, "error": blocked, "blocked": True}
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.get("tool_call_id", ""),
                            "content": self._brief(res),
                        })
                        store.record_step(session_id, round_no, "blocked", name, args, False, blocked)
                        continue

                # 研究预算：可以查资料，但不能把整次编码会话耗在浏览上
                if name in RESEARCH_TOOLS:
                    if research_calls >= MAX_RESEARCH_CALLS:
                        res = {
                            "success": False,
                            "error": f"研究预算已用完（{MAX_RESEARCH_CALLS} 次），"
                                     "请立即基于已有资料开始写代码",
                            "blocked": True,
                        }
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.get("tool_call_id", ""),
                            "content": self._brief(res, 400),
                        })
                        store.record_step(session_id, round_no, "blocked", name,
                                          args, False, res["error"])
                        continue
                    research_calls += 1

                phase = ("edit" if name in EDIT_TOOLS
                         else "verify" if name in VERIFY_TOOLS
                         else "recon")
                await progress(int((round_no - 1) / self.max_rounds * 100), phase,
                               f"第 {round_no} 轮：{name}")

                res = await self._call_tool(name, args)
                tool_calls_total += 1
                logger.info(
                    f"[coding] r{round_no} {name} success={res.get('success')} "
                    f"err={(res.get('error') or '')[:100]}"
                )
                _log_event(
                    "INFO", "coding",
                    f"r{round_no} {name} {'✓' if res.get('success') else '✗'}"
                    + (f"｜{str(res.get('error'))[:80]}" if res.get("error") else "")
                )

                # 记录改动文件
                p = self._extract_path(name, args)
                if p and name in EDIT_TOOLS:
                    if p not in files_touched:
                        files_touched.append(p)

                # 状态机：编辑 → dirty；验证成功 → verified
                if name in EDIT_TOOLS and res.get("success"):
                    dirty = True
                    verified = False
                    edit_started = True
                    # 回归守卫：改过文件后，已通过的需求全部退回待验证
                    if spec is not None and spec_store is not None:
                        try:
                            stale_n = spec_store.mark_stale(spec["id"])
                            spec = spec_store.get(spec["id"])
                            ledger = spec_store.requirements_block(spec)
                            if stale_n:
                                _log_event("INFO", "coding",
                                           f"改动后 {stale_n} 条需求转待验证"
                                           "（回归守卫）")
                        except Exception as e:  # noqa: BLE001
                            logger.debug(f"[coding] 需求账本更新失败: {e}")
                if name in VERIFY_TOOLS:
                    ok = bool(res.get("success"))
                    last_verify = {
                        "tool": name,
                        "success": ok,
                        "passed": res.get("passed"),
                        "failed": res.get("failed"),
                        "first_failure": res.get("first_failure"),
                        "stderr_tail": (res.get("stderr") or "")[-300:],
                        "stdout_tail": (res.get("stdout") or "")[-300:],
                    }
                    if ok:
                        dirty = False
                        verified = True
                        # 需求取证：这次验证覆盖到的条目才算通过
                        if spec is not None and spec_store is not None:
                            try:
                                self._record_verify(spec, spec_store, name, args, res)
                                spec = spec_store.get(spec["id"])
                                ledger = spec_store.requirements_block(spec)
                            except Exception as e:  # noqa: BLE001
                                logger.debug(f"[coding] 需求取证失败: {e}")
                    else:
                        verified = False

                sig = self._error_signature(name, res)
                if sig:
                    round_errors.append(sig)

                detail = (res.get("error")
                          or (f"passed={res.get('passed')} failed={res.get('failed')}"
                              if name == "code_run_tests" else "")
                          or "ok")
                store.record_step(session_id, round_no, phase, name, args,
                                  bool(res.get("success")), str(detail))

                history.append({
                    "role": "tool",
                    "tool_call_id": tc.get("tool_call_id", ""),
                    "content": self._brief(
                        res, RESEARCH_BRIEF_LIMITS.get(name, 900)),
                })

            # 参数截断：本轮工具都没执行，直接要求分段写入
            if need_split:
                payload_retries += 1
                await progress(int((round_no - 1) / self.max_rounds * 100),
                               "guard", "工具参数被截断，要求分段写入")
                _log_event("WARN", "coding",
                           f"r{round_no} 工具参数截断，转小步写入（{payload_retries}/2）")
                user_text = SPLIT_WRITE_HINT
                continue

            # no-progress 检测
            sig_now = round_errors[0] if round_errors else ""
            if sig_now and sig_now == last_sig:
                same_sig_rounds += 1
                if same_sig_rounds >= self.no_progress_rounds:
                    abort_reason = f"连续 {same_sig_rounds + 1} 轮同一错误未变"
                    break
            else:
                same_sig_rounds = 0
            last_sig = sig_now

            if verified and not dirty:
                # 已验证通过：下一轮若不再改，就会走"无工具调用"分支收尾
                user_text = ("验证已通过。若目标已达成，请直接给出简短总结（不要再调用工具）；"
                             "若还有未完成的验收项，请继续。")
            elif not edit_started and round_no >= recon_budget:
                # 侦察预算耗尽仍未动手：强制进入编辑，避免把轮次全耗在目录/搜索上
                user_text = ("侦察预算已用完。**立即调用 os_create_file 创建第一个文件**，"
                             "不要再做 code_tree / code_search / os_list_directory。"
                             "一次只建一个文件，建完继续下一个。")
                _log_event("WARN", "coding",
                           f"r{round_no} 侦察超预算，强制进入编辑阶段")
            elif last_verify and not last_verify["success"]:
                user_text = ("验证失败。请依据 first_failure 里的文件/行号/错误消息做最小修正，"
                             "然后重新运行验证。不要整文件重写。")
            else:
                user_text = "继续下一步。小步修改，改完立刻验证。"

        # ---------- 需求审计（规格驱动：没证据不算完成）----------
        missing_reqs: List[Dict[str, Any]] = []
        if spec is not None and spec_store is not None:
            try:
                missing_reqs = await self._audit_requirements(spec, spec_store)
                spec = spec_store.get(spec["id"])
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[coding] 需求审计失败: {e}")

        # ---------- 收尾 ----------
        success = bool(verified and not dirty)
        if missing_reqs:
            success = False
            abort_reason = ("需求未全部通过：" + "、".join(
                r["req_id"] for r in missing_reqs[:6]))
        status = "done" if success else ("aborted" if abort_reason else "failed")
        if not success and not abort_reason:
            abort_reason = f"达到最大轮次({self.max_rounds})仍未通过验证"

        diff_stat = ""
        try:
            diff_res = await self._call_tool("code_git_diff", {"stat": True})
            diff_stat = (diff_res.get("stdout") or "")[:1000]
        except Exception:  # noqa: BLE001
            pass

        store.finish_session(session_id, status, round_no, tool_calls_total,
                             files_touched, abort_reason, diff_stat)
        # 程序性记忆：成功给步骤，失败给 avoid（同一签名下累积）
        self._learn_procedure(store, session_id, recon_budget, state_values)

        logger.info(
            f"[coding] ===== 会话结束 #{session_id} status={status} "
            f"rounds={round_no} tools={tool_calls_total} files={files_touched} "
            f"error={abort_reason}"
        )
        _log_event("INFO" if success else "WARN", "coding",
                   f"会话 #{session_id} 结束：{status}｜{round_no} 轮 / {tool_calls_total} 次工具"
                   + (f"｜{abort_reason}" if abort_reason else ""))

        await progress(100 if success else int(round_no / self.max_rounds * 100),
                       "done" if success else "failed",
                       "完成" if success else (abort_reason or "未完成"))

        return {
            "success": success,
            "session_id": session_id,
            "rounds": round_no,
            "tool_calls": tool_calls_total,
            "files_changed": files_touched,
            "verified": verified,
            "last_verify": last_verify,
            "diff_stat": diff_stat,
            "spec_id": spec.get("id") if spec else None,
            "requirements": [
                {"id": r["req_id"], "text": r["text"], "status": r["status"]}
                for r in (spec or {}).get("requirements") or []
            ],
            "error": None if success else (abort_reason or "未通过验证"),
        }


__all__ = ['CodingLoop', 'CodingStore', 'ALLOWED_TOOL_NAMES']
