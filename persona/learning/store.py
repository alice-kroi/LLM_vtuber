# ============================================================
# persona/learning/store.py — 学习结果持久化
# ============================================================
# 全部落在 persona/state.db（与 PersonaState 同一个库，不新增数据库）。
# P0 只有两张表：
#   learn_episodes     情景（谁、什么时候、说了什么、结果如何）
#   learn_preferences  偏好（user_id + 槽位 + 取值 + 置信度 + 证据）
#
# 置信度累加：首次观察 0.3（不生效），之后每次独立观察 +0.2，上限 0.95。
#   → 0.3（1 次，不生效）→ 0.5（2 次，生效）
# 单次可能是玩笑，所以首次刻意压在门槛之下。
#
# 偏好按 user_id 隔离；不写 profile.yaml，不产生跨用户影响。
# ============================================================

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

CONFIDENCE_INIT = 0.3
CONFIDENCE_STEP = 0.2
CONFIDENCE_MAX = 0.95
CONFIDENCE_MIN = 0.05
CONFIDENCE_CONFLICT_PENALTY = 0.3
CONFIDENCE_ACTIVE_AT = 0.5
MAX_EVIDENCE = 20


class LearningStore:
    """两张学习表的读写（每次操作独立连接，与 StateDB 风格一致）"""

    def __init__(self, db_path: str = "persona/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS learn_episodes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT,
                    kind         TEXT NOT NULL,
                    summary      TEXT NOT NULL,
                    emotion      TEXT,
                    intensity    REAL,
                    reward       REAL,
                    ref_table    TEXT,
                    ref_id       INTEGER,
                    importance   REAL DEFAULT 0.5,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_ep_user_time
                    ON learn_episodes(user_id, created_at);

                CREATE TABLE IF NOT EXISTS learn_preferences (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    slot         TEXT NOT NULL,
                    value        TEXT NOT NULL,
                    polarity     INTEGER NOT NULL,
                    confidence   REAL DEFAULT 0.3,
                    evidence     TEXT,
                    status       TEXT DEFAULT 'active',
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, slot, value)
                );

                CREATE TABLE IF NOT EXISTS learn_procedures (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature     TEXT UNIQUE NOT NULL,
                    when_to_use   TEXT NOT NULL,
                    steps_json    TEXT NOT NULL,
                    avoid_json    TEXT,
                    done_when     TEXT,
                    alpha         REAL DEFAULT 1.0,
                    beta          REAL DEFAULT 1.0,
                    reliability   REAL DEFAULT 0.5,
                    uses          INTEGER DEFAULT 0,
                    last_used     TIMESTAMP,
                    weight        REAL DEFAULT 1.0,
                    status        TEXT DEFAULT 'candidate',
                    evidence      TEXT,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS learn_reflections (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope        TEXT NOT NULL,
                    input_ref    TEXT,
                    output_json  TEXT,
                    applied      INTEGER DEFAULT 0,
                    model        TEXT,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS learn_notes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic        TEXT NOT NULL UNIQUE,
                    summary      TEXT NOT NULL,
                    sources_json TEXT,
                    tags         TEXT,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_notes_updated
                    ON learn_notes(updated_at);
            """)

    # ---------- 情景 ----------

    def record_episode(self, user_id: Optional[str], kind: str, summary: str,
                       emotion: Optional[str] = None,
                       intensity: Optional[float] = None,
                       reward: Optional[float] = None,
                       ref_table: Optional[str] = None,
                       ref_id: Optional[int] = None,
                       importance: float = 0.5) -> int:
        """写一条情景记忆，返回 episode id（偏好证据要用）"""
        summary = (summary or "")[:200]
        with self._lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                INSERT INTO learn_episodes
                (user_id, kind, summary, emotion, intensity, reward,
                 ref_table, ref_id, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, kind, summary, emotion, intensity, reward,
                  ref_table, ref_id, importance))
            return int(cur.lastrowid)

    def recall_episodes(self, user_id: Optional[str], limit: int = 100,
                        max_age_days: int = 180) -> List[Dict[str, Any]]:
        """取该用户最近的情景条目（打分与筛选交给 episode.select）"""
        if not user_id:
            return []
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
                  - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, user_id, kind, summary, emotion, intensity, reward, "
                "importance, created_at FROM learn_episodes "
                "WHERE user_id=? AND created_at>=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 偏好 ----------

    def add_preference(self, user_id: str, slot: str, value: str,
                       polarity: int, evidence: Optional[Sequence[int]] = None) -> Dict[str, Any]:
        """观察一次偏好。

        - 新条目：confidence = 0.3（未达生效门槛）
        - 同一极性再次观察：+0.2（第二次即 0.5，生效；上限 0.95）
        - 相反极性：不加反降 0.3（矛盾观察不算强化），保留原极性
        """
        if not user_id or not slot or not value:
            return {}
        polarity = 1 if polarity >= 0 else -1
        new_ids = [int(i) for i in (evidence or [])]

        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, polarity, confidence, evidence FROM learn_preferences "
                "WHERE user_id=? AND slot=? AND value=?",
                (user_id, slot, value),
            ).fetchone()

            if row is None:
                ids = new_ids[-MAX_EVIDENCE:]
                conn.execute("""
                    INSERT INTO learn_preferences
                    (user_id, slot, value, polarity, confidence, evidence,
                     status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
                """, (user_id, slot, value, polarity, CONFIDENCE_INIT,
                      json.dumps(ids, ensure_ascii=False)))
            else:
                try:
                    ids = json.loads(row["evidence"] or "[]")
                except Exception:  # noqa: BLE001
                    ids = []
                ids.extend(i for i in new_ids if i not in ids)
                ids = ids[-MAX_EVIDENCE:]

                if int(row["polarity"]) == polarity:
                    conf = min(CONFIDENCE_MAX, row["confidence"] + CONFIDENCE_STEP)
                else:
                    conf = max(CONFIDENCE_MIN, row["confidence"] - CONFIDENCE_CONFLICT_PENALTY)

                conn.execute(
                    "UPDATE learn_preferences SET confidence=?, evidence=?, "
                    "status='active', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (round(conf, 3), json.dumps(ids, ensure_ascii=False), row["id"]),
                )

            final = conn.execute(
                "SELECT * FROM learn_preferences WHERE user_id=? AND slot=? AND value=?",
                (user_id, slot, value),
            ).fetchone()
            return dict(final) if final else {}

    def preferences_for(self, user_id: Optional[str],
                        min_confidence: float = CONFIDENCE_ACTIVE_AT,
                        limit: int = 5) -> List[Dict[str, Any]]:
        """当前对话对象生效中的偏好（按置信度降序，最多 limit 条）"""
        if not user_id:
            return []
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT slot, value, polarity, confidence, evidence FROM learn_preferences "
                "WHERE user_id=? AND status='active' AND confidence>=? "
                "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
                (user_id, min_confidence, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_preferences(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """审计用：包含未生效条目"""
        sql = ("SELECT id, user_id, slot, value, polarity, confidence, status, "
               "evidence, updated_at FROM learn_preferences")
        args: tuple = ()
        if user_id:
            sql += " WHERE user_id=?"
            args = (user_id,)
        sql += " ORDER BY confidence DESC, updated_at DESC"
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ---------- 程序 ----------

    @staticmethod
    def _proc_dict(row: Any) -> Dict[str, Any]:
        item = dict(row)
        for key, src in (("steps", "steps_json"), ("avoid", "avoid_json"),
                         ("evidence_ids", "evidence")):
            try:
                item[key] = json.loads(item.get(src) or "[]")
            except Exception:  # noqa: BLE001
                item[key] = []
        return item

    def upsert_procedure(self, signature: str, when_to_use: str,
                         steps: Optional[Sequence[str]] = None,
                         done_when: str = "",
                         avoid: Optional[Sequence[str]] = None,
                         evidence: Optional[Sequence[Any]] = None,
                         success: bool = True) -> Dict[str, Any]:
        """写入/更新一条流程（Beta 后验 + 证据去重）。

        - 新流程：α=1(+1 成功)、β=1(+1 失败)，status=candidate
        - 同一 session 重复抽取：只加证据，不重复计分
        - 晋升 active：≥3 次成功轨迹且 reliability ≥ 0.75
        """
        if not signature:
            return {}
        new_ids = [str(i) for i in (evidence or [])]
        steps = list(steps or [])
        avoid = list(avoid or [])

        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM learn_procedures WHERE signature=?", (signature,)
            ).fetchone()

            if row is None:
                alpha = 1.0 + (1.0 if success else 0.0)
                beta = 1.0 + (0.0 if success else 1.0)
                conn.execute("""
                    INSERT INTO learn_procedures
                    (signature, when_to_use, steps_json, avoid_json, done_when,
                     alpha, beta, reliability, uses, weight, status, evidence)
                    VALUES (?,?,?,?,?,?,?,?,0,1.0,'candidate',?)
                """, (signature, when_to_use,
                      json.dumps(steps, ensure_ascii=False),
                      json.dumps(avoid, ensure_ascii=False),
                      done_when, alpha, beta, round(alpha / (alpha + beta), 3),
                      json.dumps(new_ids, ensure_ascii=False)))
            else:
                base = self._proc_dict(row)
                if new_ids and set(new_ids).issubset(set(base["evidence_ids"])):
                    return base          # 同一个 session 不重复计分

                alpha = float(base["alpha"]) + (1.0 if success else 0.0)
                beta = float(base["beta"]) + (0.0 if success else 1.0)
                reliability = round(alpha / (alpha + beta), 3)
                old_steps = base["steps"]
                old_avoid = base["avoid"]
                merged_steps = steps if (success and steps) else old_steps
                merged_avoid = old_avoid + [a for a in avoid if a not in old_avoid]
                merged_avoid = merged_avoid[-5:]
                ids = base["evidence_ids"] + [i for i in new_ids
                                              if i not in base["evidence_ids"]]
                ids = ids[-20:]

                status = base["status"]
                successes = int(alpha - 1)
                if successes >= 3 and reliability >= 0.75:
                    status = "active"
                elif status == "active" and reliability < 0.5:
                    status = "candidate"

                conn.execute("""
                    UPDATE learn_procedures SET
                        when_to_use=?, steps_json=?, avoid_json=?, done_when=?,
                        alpha=?, beta=?, reliability=?, status=?, evidence=?
                    WHERE signature=?
                """, (when_to_use or base["when_to_use"],
                      json.dumps(merged_steps, ensure_ascii=False),
                      json.dumps(merged_avoid, ensure_ascii=False),
                      done_when or base["done_when"],
                      alpha, beta, reliability, status,
                      json.dumps(ids, ensure_ascii=False), signature))

            final = conn.execute(
                "SELECT * FROM learn_procedures WHERE signature=?", (signature,)
            ).fetchone()
            return self._proc_dict(final) if final else {}

    def recall_procedures(self, signature: Optional[str] = None, *,
                          signature_prefix: Optional[str] = None,
                          state_values: Optional[Sequence[float]] = None,
                          min_effective: float = 0.35) -> Optional[Dict[str, Any]]:
        """召回一条可用的流程；召回即计一次 uses（用于晋升）。

        Args:
            signature: 精确匹配
            signature_prefix: 同任务类型前缀（如 `编码|新建能力域|`）
            state_values: (energy, social_need, curiosity)；给了就按 E/S/C 档位距离选最近
        """
        if not signature and not signature_prefix:
            return None
        from persona.learning.episode import decay_weight
        from persona.learning.procedure import tier_distance

        if signature:
            where, where_args = "signature=?", (signature,)
        else:
            where, where_args = "signature LIKE ?", (signature_prefix + "%",)

        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM learn_procedures WHERE status!='retired' AND {where}",
                where_args,
            ).fetchall()
            if not rows:
                return None

            best = None   # ((档位距离, -reliability), item, effective, weight)
            for row in rows:
                item = self._proc_dict(row)
                weight = decay_weight(item.get("last_used") or item.get("created_at"))
                effective = round(float(item["reliability"]) * weight, 3)
                if effective < min_effective:
                    conn.execute(
                        "UPDATE learn_procedures SET status='retired', weight=? WHERE id=?",
                        (weight, item["id"]))
                    continue
                distance = (tier_distance(item["signature"], state_values)
                            if state_values else 0.0)
                key = (distance, -float(item["reliability"]))
                if best is None or key < best[0]:
                    best = (key, item, effective, weight)
            if best is None:
                return None

            _, item, effective, weight = best
            conn.execute("UPDATE learn_procedures SET uses=uses+1, weight=?, "
                         "last_used=CURRENT_TIMESTAMP WHERE id=?", (weight, item["id"]))
            item["uses"] = int(item.get("uses") or 0) + 1
            item["weight"] = weight
            item["effective"] = effective
            return item

    def list_procedures(self, limit: int = 50) -> List[Dict[str, Any]]:
        """审计/面板用：全部流程（含 candidate / retired）"""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM learn_procedures ORDER BY reliability DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._proc_dict(r) for r in rows]

    # ---------- 巩固（P2：纯规则，不调 LLM） ----------

    @staticmethod
    def _norm_value(value: str) -> str:
        """偏好取值归一：去空白/标点，用于找出"同义重复"条目"""
        return re.sub(r"[\s，。,.!?；;:：~～、]+", "", value or "").lower()

    def duplicate_preference_groups(self) -> List[List[Dict[str, Any]]]:
        """找出同一 user/slot/极性下，归一化后取值相同的重复条目"""
        buckets: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in self.all_preferences():
            if row.get("status") != "active":
                continue
            key = (row["user_id"], row["slot"],
                   self._norm_value(row["value"]), int(row["polarity"]))
            buckets.setdefault(key, []).append(row)
        return [group for group in buckets.values() if len(group) > 1]

    def merge_preferences(self, groups: List[List[Dict[str, Any]]],
                          dry_run: bool = False) -> int:
        """合并重复偏好：保留置信度最高的一条，证据取并集，条数下降"""
        merged = 0
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for group in groups or []:
                if len(group) < 2:
                    continue
                keeper = max(group, key=lambda r: (r["confidence"], -r["id"]))
                drop = [r["id"] for r in group if r["id"] != keeper["id"]]
                evidence: List[Any] = []
                for row in group:
                    try:
                        ids = json.loads(row.get("evidence") or "[]")
                    except Exception:  # noqa: BLE001
                        ids = []
                    evidence.extend(i for i in ids if i not in evidence)
                merged += len(drop)
                if dry_run:
                    continue
                conn.execute(
                    "UPDATE learn_preferences SET confidence=?, evidence=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (max(float(r["confidence"]) for r in group),
                     json.dumps(evidence[-MAX_EVIDENCE:], ensure_ascii=False),
                     keeper["id"]),
                )
                placeholders = ",".join("?" * len(drop))
                conn.execute(
                    f"DELETE FROM learn_preferences WHERE id IN ({placeholders})", drop)
        return merged

    def prune_episodes(self, max_age_days: int = 30,
                       importance_threshold: float = 0.4,
                       dry_run: bool = False) -> int:
        """淘汰低重要度旧情景；被偏好引用的证据不删"""
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
                  - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            referenced = set()
            for row in conn.execute(
                    "SELECT evidence FROM learn_preferences WHERE evidence IS NOT NULL"):
                try:
                    referenced.update(int(x) for x in json.loads(row["evidence"] or "[]"))
                except Exception:  # noqa: BLE001
                    continue
            rows = conn.execute(
                "SELECT id FROM learn_episodes WHERE created_at < ? AND importance < ?",
                (cutoff, importance_threshold),
            ).fetchall()
            ids = [r["id"] for r in rows if r["id"] not in referenced]
            if ids and not dry_run:
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM learn_episodes WHERE id IN ({placeholders})", ids)
        return len(ids)

    def retire_stale_procedures(self, min_effective: float = 0.35,
                                dry_run: bool = False) -> List[str]:
        """退役 effective（可靠性×时间衰减）低于阈值的流程"""
        from persona.learning.episode import decay_weight

        retired: List[str] = []
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM learn_procedures WHERE status!='retired'").fetchall()
            for row in rows:
                item = self._proc_dict(row)
                weight = decay_weight(item.get("last_used") or item.get("created_at"))
                effective = float(item["reliability"]) * weight
                if effective >= min_effective:
                    continue
                retired.append(item["signature"])
                if not dry_run:
                    conn.execute(
                        "UPDATE learn_procedures SET status='retired', weight=? WHERE id=?",
                        (weight, item["id"]))
        return retired

    # ---------- 反思日志 ----------

    def log_reflection(self, scope: str, input_ref: Optional[str],
                       output: Any, applied: bool = True,
                       model: str = "rules") -> int:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO learn_reflections(scope, input_ref, output_json, applied, model)"
                " VALUES (?,?,?,?,?)",
                (scope, input_ref, json.dumps(output, ensure_ascii=False),
                 1 if applied else 0, model))
            return int(cur.lastrowid)

    def last_reflection_at(self, scope: str = "nightly") -> Optional[str]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT created_at FROM learn_reflections WHERE scope=? "
                "ORDER BY id DESC LIMIT 1", (scope,)).fetchone()
            return row[0] if row else None

    def list_reflections(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM learn_reflections ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["output"] = json.loads(item.get("output_json") or "{}")
            except Exception:  # noqa: BLE001
                item["output"] = {}
            out.append(item)
        return out

    # ---------- 联网研究笔记 ----------

    @staticmethod
    def _note_dict(row: Any) -> Dict[str, Any]:
        item = dict(row)
        try:
            item["sources"] = json.loads(item.get("sources_json") or "[]")
        except Exception:  # noqa: BLE001
            item["sources"] = []
        item.pop("sources_json", None)
        return item

    def save_note(self, topic: str, summary: str,
                  sources: Optional[Sequence[str]] = None,
                  tags: Optional[str] = None) -> Dict[str, Any]:
        """保存/更新一条研究笔记（同一 topic 覆盖旧版本）。"""
        topic = (topic or "").strip()
        summary = (summary or "").strip()
        if not topic or not summary:
            return {}
        topic = topic[:200]
        summary = summary[:4000]
        sources_json = json.dumps(list(sources or [])[:20], ensure_ascii=False)

        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("""
                INSERT INTO learn_notes (topic, summary, sources_json, tags, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(topic) DO UPDATE SET
                    summary=excluded.summary,
                    sources_json=excluded.sources_json,
                    tags=excluded.tags,
                    updated_at=CURRENT_TIMESTAMP
            """, (topic, summary, sources_json, (tags or "")[:200]))
            row = conn.execute(
                "SELECT * FROM learn_notes WHERE topic=?", (topic,)
            ).fetchone()
        return self._note_dict(row) if row else {}

    def recent_notes(self, query: Optional[str] = None,
                     limit: int = 5) -> List[Dict[str, Any]]:
        """取最近的研究笔记；给了 query 就按关键词做低成本过滤。"""
        sql = ("SELECT id, topic, summary, sources_json, tags, created_at, updated_at "
               "FROM learn_notes")
        args: List[Any] = []
        if query:
            tokens = [t for t in re.split(r"[\s,，。.!！？?、/;；]+", query)
                      if len(t) >= 2][:6]
            if not tokens:
                return []
            clauses = []
            for token in tokens:
                clauses.append("(topic LIKE ? OR summary LIKE ?)")
                args.extend([f"%{token}%", f"%{token}%"])
            sql += " WHERE " + " OR ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
        return [self._note_dict(r) for r in rows]

    # ---------- 统计 ----------

    def stats(self) -> Dict[str, Any]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            episodes = conn.execute("SELECT COUNT(*) FROM learn_episodes").fetchone()[0]
            prefs = conn.execute("SELECT COUNT(*) FROM learn_preferences").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM learn_preferences WHERE status='active' "
                "AND confidence>=?", (CONFIDENCE_ACTIVE_AT,)).fetchone()[0]
            users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM learn_preferences").fetchone()[0]
            procedures = conn.execute(
                "SELECT COUNT(*) FROM learn_procedures").fetchone()[0]
            active_proc = conn.execute(
                "SELECT COUNT(*) FROM learn_procedures WHERE status='active'").fetchone()[0]
            reflections = conn.execute(
                "SELECT COUNT(*) FROM learn_reflections").fetchone()[0]
            notes = conn.execute("SELECT COUNT(*) FROM learn_notes").fetchone()[0]
        return {"episodes": episodes, "preferences": prefs,
                "active_preferences": active, "users": users,
                "procedures": procedures, "active_procedures": active_proc,
                "reflections": reflections, "notes": notes}


__all__ = ["LearningStore", "CONFIDENCE_ACTIVE_AT"]
