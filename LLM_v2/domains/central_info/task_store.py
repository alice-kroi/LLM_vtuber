# ============================================================
# LLM_v2/domains/central_info/task_store.py — 运行时任务台账
# ============================================================
# 只存"可变层"：任务进行到哪一步。问题/需求不在这里，
# 它们由 spec_workflow 的规格投影过来（中央信息只读）。
#
# 借鉴 dsh-goal 的三条硬规则：
#   1. CAS（compare-and-set）：写入必须带 revision，陈旧视图会被拒绝
#   2. 相位机：pending / running / blocked / done / failed；done、failed 终态
#   3. 每次写入追加事件，可审计"谁在什么时候改了什么"
# ============================================================

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "persona" / "state.db"

PHASE_PENDING = "pending"
PHASE_RUNNING = "running"
PHASE_BLOCKED = "blocked"
PHASE_DONE = "done"
PHASE_FAILED = "failed"

PHASES = (PHASE_PENDING, PHASE_RUNNING, PHASE_BLOCKED, PHASE_DONE, PHASE_FAILED)
TERMINAL_PHASES = (PHASE_DONE, PHASE_FAILED)
# 只有思维脑可以把任务关闭（进入终态）；其他脑可以判定完成，但关账归思维脑
CLOSE_ACTOR = "will_engine"
# 允许的相位流转（终态不可离开：done 不可回退，返工要新任务）
TRANSITIONS = {
    PHASE_PENDING: {PHASE_PENDING, PHASE_RUNNING, PHASE_BLOCKED,
                    PHASE_DONE, PHASE_FAILED},
    PHASE_RUNNING: {PHASE_RUNNING, PHASE_BLOCKED, PHASE_DONE, PHASE_FAILED},
    PHASE_BLOCKED: {PHASE_BLOCKED, PHASE_RUNNING, PHASE_FAILED},
    PHASE_DONE: set(),
    PHASE_FAILED: set(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS central_tasks (
    task_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    phase       TEXT NOT NULL DEFAULT 'pending',
    percent     INTEGER NOT NULL DEFAULT 0,
    stage       TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    owner       TEXT NOT NULL DEFAULT '',
    revision    INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS central_task_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL
);
"""


def _now() -> float:
    return time.time()


class TaskStore:
    """任务台账（中央信息的可变层）"""

    def __init__(self, db_path: Any = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """轻量迁移：老库补列，不破坏已有数据。"""
        try:
            cols = {r[1] for r in self._conn.execute(
                "PRAGMA table_info(central_tasks)").fetchall()}
            if "plan_json" not in cols:
                self._conn.execute(
                    "ALTER TABLE central_tasks ADD COLUMN plan_json TEXT DEFAULT ''")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[task_store] 迁移失败: {e}")

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 读 ----------

    @staticmethod
    def _item(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        if not row:
            return {}
        item = dict(row)
        raw = item.pop("plan_json", "") or ""
        try:
            item["plan"] = json.loads(raw) if raw else []
        except Exception:  # noqa: BLE001
            item["plan"] = []
        return item

    def get(self, task_id: str) -> Dict[str, Any]:
        return self._item(self._conn.execute(
            "SELECT * FROM central_tasks WHERE task_id=?", (task_id,)).fetchone())

    def list(self, limit: int = 20, include_terminal: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM central_tasks"
        params: List[Any] = []
        if not include_terminal:
            sql += " WHERE phase NOT IN (?,?)"
            params = [PHASE_DONE, PHASE_FAILED]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 20), 100)))
        return [self._item(r) for r in
                self._conn.execute(sql, params).fetchall()]

    def events(self, task_id: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        if task_id:
            rows = self._conn.execute(
                "SELECT * FROM central_task_events WHERE task_id=?"
                " ORDER BY seq DESC LIMIT ?", (task_id, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM central_task_events ORDER BY seq DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def render_table(self, limit: int = 20,
                     include_terminal: bool = False) -> str:
        """GFM 表格（DSH/dsh-pua 同款：状态、进度用表格渲染）"""
        rows = self.list(limit, include_terminal)
        if not rows:
            return "（当前没有进行中的任务）"
        lines = ["| 任务 | 相位 | 进度 | 阶段 | 说明 | rev |",
                 "|---|---|---|---|---|---|"]
        for t in rows:
            detail = (t["detail"] or "").replace("|", "／").replace("\n", " ")[:40]
            lines.append(
                f"| {t['title']} | {t['phase']} | {t['percent']}% | "
                f"{t['stage'] or '-'} | {detail or '-'} | {t['revision']} |")
        return "\n".join(lines)

    # ---------- 写 ----------

    def create(self, title: str, owner: str = "", stage: str = "",
               detail: str = "", phase: str = PHASE_RUNNING,
               plan: Optional[Sequence[Dict[str, Any]]] = None
               ) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            return {}
        if phase not in PHASES:
            phase = PHASE_RUNNING
        task_id = uuid.uuid4().hex[:12]
        now = _now()
        self._conn.execute(
            "INSERT INTO central_tasks(task_id, title, phase, percent, stage,"
            " detail, owner, revision, created_at, updated_at, plan_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, title[:200], phase, 0, stage[:60], detail[:500],
             owner[:60], 1, now, now,
             json.dumps(plan, ensure_ascii=False)[:8000] if plan else ""))
        self._conn.commit()
        self._event(task_id, "created", owner, title[:200])
        return self.get(task_id)

    def update(self, task_id: str, revision: Any, *, actor: str = "",
               phase: str = "", percent: Any = None, stage: str = "",
               detail: str = "", plan: Optional[Sequence[Dict[str, Any]]] = None
               ) -> Dict[str, Any]:
        """CAS 更新。返回 {ok, task} 或 {ok:False, error, task}"""
        task = self.get(task_id)
        if not task:
            return {"ok": False, "error": f"未知任务 {task_id}"}
        try:
            rev = int(revision)
        except (TypeError, ValueError):
            return {"ok": False, "error": "revision 必须是整数",
                    "task": task}
        if rev != task["revision"]:
            return {"ok": False,
                    "error": f"revision 过期：当前 {task['revision']}，传入 {rev}；"
                             "请先读 ci_tasks 再写",
                    "task": task}

        new_phase = (phase or task["phase"]).strip().lower()
        if new_phase not in PHASES:
            return {"ok": False, "error": f"未知相位 {new_phase}", "task": task}
        if new_phase in TERMINAL_PHASES and actor != CLOSE_ACTOR:
            return {"ok": False,
                    "error": f"只有 {CLOSE_ACTOR} 可以关闭任务；"
                             f"{actor or '未知来源'} 只能推进进度",
                    "task": task}
        allowed = TRANSITIONS.get(task["phase"], set())
        if new_phase not in allowed:
            return {"ok": False,
                    "error": f"非法相位流转 {task['phase']} → {new_phase}"
                             + ("（终态不可回退，返工请新建任务）"
                                if task["phase"] in TERMINAL_PHASES else ""),
                    "task": task}

        new_percent = task["percent"]
        if percent is not None:
            try:
                new_percent = max(0, min(int(percent), 100))
            except (TypeError, ValueError):
                return {"ok": False, "error": "percent 必须是整数", "task": task}
        # 相位兜底：done 一律 100%，pending 归零
        if new_phase == PHASE_DONE:
            new_percent = 100

        if plan is not None:
            self._conn.execute(
                "UPDATE central_tasks SET plan_json=? WHERE task_id=?",
                (json.dumps(list(plan), ensure_ascii=False)[:8000], task_id))
        self._conn.execute(
            "UPDATE central_tasks SET phase=?, percent=?, stage=?, detail=?,"
            " revision=revision+1, updated_at=? WHERE task_id=?",
            (new_phase, new_percent,
             (stage or task["stage"])[:60],
             (detail or task["detail"])[:500],
             _now(), task_id))
        self._conn.commit()
        updated = self.get(task_id)
        self._event(task_id, f"{task['phase']}→{new_phase}", actor,
                    (detail or "")[:200])
        return {"ok": True, "task": updated}

    def close(self, task_id: str, ok: bool = True, detail: str = "",
              actor: str = "will_engine") -> Dict[str, Any]:
        """收尾任务（思维脑判定）：不要求调用方持有 revision。

        幂等：任务已是终态时直接返回当前记录。
        """
        task = self.get(task_id)
        if not task:
            return {"ok": False, "error": f"未知任务 {task_id}"}
        if task["phase"] in TERMINAL_PHASES:
            return {"ok": True, "task": task, "already": True}
        return self.update(
            task_id, task["revision"], actor=actor,
            phase=PHASE_DONE if ok else PHASE_FAILED,
            percent=100 if ok else None,
            stage="done" if ok else "failed",
            detail=detail,
        )

    def _event(self, task_id: str, kind: str, actor: str, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO central_task_events(task_id, kind, actor, detail, at)"
            " VALUES(?,?,?,?,?)",
            (task_id, kind, actor[:60], detail[:500], _now()))
        self._conn.commit()


__all__ = [
    "TaskStore", "PHASES", "TERMINAL_PHASES", "TRANSITIONS",
    "PHASE_PENDING", "PHASE_RUNNING", "PHASE_BLOCKED",
    "PHASE_DONE", "PHASE_FAILED", "DEFAULT_DB",
]
