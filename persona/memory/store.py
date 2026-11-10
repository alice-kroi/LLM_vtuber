# ============================================================
# persona/memory/store.py — 记忆库读写（SQLite）
# ============================================================
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from persona.memory.schema import connect
from persona.memory.text import now_str, phrase_containment, similarity

logger = logging.getLogger(__name__)


class MemoryStore:
    """记忆库：mem_items / turn_summaries / mem_proposals / mem_audit。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = connect(db_path)

    # ---------- 基础 ----------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        for key in ("evidence_json", "entities_json"):
            if key in data and isinstance(data[key], str):
                try:
                    data[key] = json.loads(data[key])
                except Exception:  # noqa: BLE001
                    pass
        return data

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:24]

    # ---------- 长期条目 ----------

    def add_item(
        self,
        track: str,
        text: str,
        *,
        scope: str = "project",
        scope_key: str = "",
        summary: str = "",
        importance: float = 0.5,
        confidence: float = 0.5,
        status: str = "active",
        source: str = "agent",
        session_id: str = "",
        turn_id: str = "",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> str:
        item_id = uuid.uuid4().hex
        now = now_str()
        with self._lock:
            self._conn.execute(
                """INSERT INTO mem_items
                   (id, track, scope, scope_key, text, summary, importance,
                    confidence, status, source, session_id, turn_id,
                    evidence_json, hash, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, track, scope, scope_key, str(text), str(summary),
                 float(importance), float(confidence), status, source,
                 session_id, turn_id,
                 json.dumps(evidence or {}, ensure_ascii=False),
                 self._hash(text), now, now),
            )
            self._conn.commit()
        return item_id

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mem_items WHERE id=?", (item_id,)).fetchone()
        return self._dict(row)

    def list_items(
        self,
        *,
        track: Optional[str] = None,
        status: str = "active",
        scope: Optional[str] = None,
        scope_key: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM mem_items WHERE status=?"
        args: List[Any] = [status]
        if track:
            sql += " AND track=?"
            args.append(track)
        if scope:
            sql += " AND scope=?"
            args.append(scope)
        if scope_key is not None:
            sql += " AND scope_key=?"
            args.append(scope_key)
        sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        args.append(max(1, min(2000, int(limit or 500))))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._dict(r) for r in rows if r is not None]

    def update_item_usage(self, item_ids: List[str]) -> None:
        if not item_ids:
            return
        now = now_str()
        with self._lock:
            for item_id in item_ids:
                self._conn.execute(
                    "UPDATE mem_items SET use_count=use_count+1, "
                    "last_used_at=? WHERE id=?", (now, item_id))
            self._conn.commit()

    # ---------- 轮次摘要 ----------

    def add_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        source: str,
        visibility: str,
        user_id: str,
        project_key: str,
        user_text: str,
        assistant_text: str,
        summary: str,
        importance: float = 0.5,
    ) -> str:
        summary_id = uuid.uuid4().hex
        now = now_str()
        with self._lock:
            self._conn.execute(
                """INSERT INTO turn_summaries
                   (id, session_id, turn_id, source, visibility, user_id,
                    project_key, user_text, assistant_text, summary,
                    importance, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (summary_id, session_id, turn_id, source, visibility,
                 user_id, project_key, str(user_text), str(assistant_text),
                 str(summary), float(importance), now, now),
            )
            self._conn.commit()
        return summary_id

    def update_turn(self, summary_id: str, *, summary: str,
                    entities: Optional[List[str]] = None,
                    importance: float = 0.5) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE turn_summaries
                   SET summary=?, entities_json=?, importance=?, updated_at=?
                   WHERE id=?""",
                (str(summary), json.dumps(entities or [], ensure_ascii=False),
                 float(importance), now_str(), summary_id))
            self._conn.commit()

    def get_summary(self, summary_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM turn_summaries WHERE id=?", (summary_id,)).fetchone()
        return self._dict(row)

    def recent_summaries(
        self,
        *,
        limit: int = 400,
        visibility: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM turn_summaries WHERE 1=1"
        args: List[Any] = []
        if visibility:
            if visibility == "private" and user_id:
                sql += (" AND ((visibility='private' AND user_id=?)"
                        " OR visibility IN ('group','public'))")
                args.append(user_id)
            elif visibility in ("group", "public"):
                sql += " AND visibility IN (?, 'public')"
                args.append(visibility)
            else:
                sql += " AND visibility=?"
                args.append(visibility)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(2000, int(limit or 400))))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._dict(r) for r in rows if r is not None]

    # ---------- 建议队列 ----------

    def add_proposal(self, kind: str, text: str, *, reason: str = "",
                     evidence: Optional[Dict[str, Any]] = None,
                     frequency: int = 1) -> str:
        text = str(text or "").strip()
        with self._lock:
            existing = self._conn.execute(
                """SELECT id, frequency FROM mem_proposals
                   WHERE kind=? AND text=? AND status='pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (kind, text)).fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE mem_proposals SET frequency=? WHERE id=?",
                    (int(existing["frequency"]) + max(1, int(frequency)),
                     existing["id"]))
                self._conn.commit()
                return str(existing["id"])
            # 近重复合并：同一轨道下表述相近的建议只保留一条，文本取更完整的
            for row in self._conn.execute(
                    """SELECT id, text, frequency FROM mem_proposals
                       WHERE kind=? AND status='pending'""", (kind,)).fetchall():
                old = str(row["text"] or "")
                if (phrase_containment(text, old) >= 0.8
                        or similarity(text, old) >= 0.7):
                    merged = text if len(text) > len(old) else old
                    self._conn.execute(
                        "UPDATE mem_proposals SET text=?, frequency=? WHERE id=?",
                        (merged,
                         int(row["frequency"]) + max(1, int(frequency)),
                         row["id"]))
                    self._conn.commit()
                    return str(row["id"])
            proposal_id = uuid.uuid4().hex
            self._conn.execute(
                """INSERT INTO mem_proposals
                   (id, kind, text, reason, evidence_json, frequency,
                    status, created_at)
                   VALUES (?,?,?,?,?,?, 'pending', ?)""",
                (proposal_id, kind, text, str(reason),
                 json.dumps(evidence or {}, ensure_ascii=False),
                 max(1, int(frequency)), now_str()),
            )
            self._conn.commit()
        return proposal_id

    def list_proposals(self, status: str = "pending",
                       limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM mem_proposals WHERE status=?
                   ORDER BY frequency DESC, created_at DESC LIMIT ?""",
                (status, max(1, min(200, int(limit or 50))))).fetchall()
        return [self._dict(r) for r in rows if r is not None]

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mem_proposals WHERE id=?", (proposal_id,)).fetchone()
        return self._dict(row)

    def decide_proposal(self, proposal_id: str, status: str, *,
                        text: Optional[str] = None,
                        actor: str = "user") -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mem_proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                return False
            new_text = str(text).strip() if text is not None else str(row["text"])
            self._conn.execute(
                """UPDATE mem_proposals
                   SET text=?, status=?, decided_at=?, decided_by=?
                   WHERE id=?""",
                (new_text, status, now_str(), actor, proposal_id))
            self._conn.commit()
        return True

    # ---------- 审计 / 状态 ----------

    def audit(self, action: str, *, item_id: str = "", actor: str = "",
              reason: str = "", result: str = "",
              session_id: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO mem_audit
                   (action, item_id, actor, reason, result, session_id, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (action, item_id, actor, reason, result, session_id, now_str()))
            self._conn.commit()

    def recent_audit(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM mem_audit ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit or 20))),)).fetchall()
        return [self._dict(r) for r in rows if r is not None]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            items = self._conn.execute(
                "SELECT track, status, COUNT(*) AS n FROM mem_items "
                "GROUP BY track, status").fetchall()
            turns = self._conn.execute(
                "SELECT COUNT(*) AS n FROM turn_summaries").fetchone()["n"]
            proposals = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM mem_proposals "
                "GROUP BY status").fetchall()
            audits = self._conn.execute(
                "SELECT COUNT(*) AS n FROM mem_audit").fetchone()["n"]
        return {
            "db_path": self.db_path,
            "items": [dict(r) for r in items],
            "turn_summaries": int(turns),
            "proposals": [dict(r) for r in proposals],
            "audit_rows": int(audits),
        }


__all__ = ["MemoryStore"]
