# ============================================================
# LLM_v2/core/will_soul.py — 意志脑四件套（灵魂层）
# ============================================================
# 本层只做"意志脑的记忆与上下文"，不改情绪/心情/学习：
#   · 状态与心情：只读 runtime.state / Self.snapshot()，从不写 PAD
#   · 经验：编码类复用 persona/learning 的 learn_procedures；
#           非编码动作从 will_decisions + will_outcomes 现场聚合
#   · 情绪：仍由 persona/self.py + persona/emotion.py 驱动
#
# 四件套：
#   ① WillIdentity   从 profile.yaml 的 will_engine 段读（未知键原样保留）
#   ② WillMemory     will_decisions / will_outcomes + 启动回放
#   ③ WillExperience 由决策结果聚合（不新建 will_experience，避免两套技能库）
#   ④ WillKnowledge  能力清单 + 兴趣 + lorebook
#
# 设计见 docs/WILL_ENGINE_SOUL_ARCHITECTURE.md
# ============================================================

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_REPLAY_ROWS = 20
MAX_REPLAY_CHARS = 900
MAX_EXPERIENCE_ROWS = 3
MAX_CONTEXT_CHARS = 2600
DECAY_LAMBDA = 0.02          # 半衰期 ≈ 35 天
MIN_EXPERIENCE_WEIGHT = 0.35
# bond：内心侧的关系感（与 persona/relationship.py 的对外关系变量分开）
BOND_DECAY_LAMBDA = 0.01     # 半衰期 ≈ 69 天
BOND_FAMILIARITY_STEP = 0.02
BOND_ATTACHMENT_STEP = 0.01
BOND_MIN_VISIBLE = 0.05
ROUTINE_WINDOW_MIN = 5

_KNOWN_IDENTITY_KEYS = {
    "narrative_identity", "drives", "boundaries", "tone_inner",
    "preferred_interests", "routine", "quiet_hours",
}

_ACTION_HINTS = {
    "social_action": "找人说话",
    "explore_interest": "探索兴趣",
    "watch_video": "刷视频",
    "code_action": "写代码",
    "rest": "休息",
    "diary": "写日记",
    "execute_tool": "执行工具",
}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_since(stamp: Optional[str]) -> float:
    if not stamp:
        return 0.0
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0.0
    return max(0.0, (_utcnow() - dt).total_seconds() / 86400.0)


class WillSoul:
    """意志脑的灵魂层：身份 / 决策记忆 / 经验 / 知识 / 上下文装配"""

    def __init__(self, runtime, db_path: str = "persona/state.db",
                 profile_path: str = "persona/profile.yaml"):
        self.runtime = runtime
        self.db_path = Path(db_path)
        self.profile_path = Path(profile_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.identity = self._load_identity()
        self._init_schema()

    # ---------- ① 人格件 WillIdentity ----------

    def _load_identity(self) -> Dict[str, Any]:
        if not self.profile_path.exists():
            return {}
        try:
            import yaml
            raw = yaml.safe_load(self.profile_path.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[will_soul] 读取人格段失败（回落为空）: {e}")
            return {}
        section = raw.get("will_engine")
        return section if isinstance(section, dict) else {}

    def identity_block(self, max_chars: int = 700) -> str:
        """人格块：叙事认同 + 动机 + 红线 + 语气 + 未知扩展键"""
        if not self.identity:
            return ""
        lines: List[str] = ["【我是谁（内心）】"]
        narrative = str(self.identity.get("narrative_identity") or "").strip()
        if narrative:
            lines.append(narrative)

        drives = self.identity.get("drives") or []
        if isinstance(drives, list) and drives:
            lines.append("【我在意的】")
            for d in drives:
                if not isinstance(d, dict):
                    continue
                name = d.get("name") or "?"
                weight = d.get("weight")
                hint = d.get("hint") or ""
                try:
                    w = f"（{float(weight):.2f}）"
                except (TypeError, ValueError):
                    w = ""
                lines.append(f"- {name}{w}：{hint}")

        boundaries = self.identity.get("boundaries") or []
        if isinstance(boundaries, list) and boundaries:
            lines.append("【我不做的事】")
            lines.extend(f"- {b}" for b in boundaries[:5])

        tone = str(self.identity.get("tone_inner") or "").strip()
        if tone:
            lines.append(f"【内心语气】{tone}")

        preferred = self.identity.get("preferred_interests") or []
        if isinstance(preferred, list) and preferred:
            lines.append(f"【偏好领域】{'、'.join(str(x) for x in preferred[:6])}")

        # 未知键原样带上：用户加字段即刻生效，不需要改代码
        for key, value in self.identity.items():
            if key in _KNOWN_IDENTITY_KEYS or key.startswith("_"):
                continue
            text = str(value).strip().replace("\n", " ")
            if text:
                lines.append(f"【{key}】{text[:120]}")

        return self._clip("\n".join(lines), max_chars)

    # ---------- 表结构 ----------

    def _init_schema(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS will_decisions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    decided_at    TEXT NOT NULL,
                    trigger       TEXT,
                    mode          TEXT,
                    state_snapshot TEXT,
                    action_type   TEXT,
                    action_target TEXT,
                    reasoning     TEXT,
                    confidence    REAL,
                    task_id       TEXT,
                    created_at    TEXT
                );
                CREATE TABLE IF NOT EXISTS will_outcomes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id  INTEGER NOT NULL,
                    task_id      TEXT,
                    finished_at  TEXT,
                    success      INTEGER,
                    duration_sec REAL,
                    error        TEXT,
                    reward_delta REAL DEFAULT 0,
                    note         TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_will_dec_at  ON will_decisions(decided_at);
                CREATE INDEX IF NOT EXISTS idx_will_out_dec ON will_outcomes(decision_id);

                CREATE TABLE IF NOT EXISTS will_bond (
                    user_id      TEXT PRIMARY KEY,
                    familiarity  REAL DEFAULT 0.1,
                    attachment   REAL DEFAULT 0.05,
                    interactions INTEGER DEFAULT 0,
                    last_seen    TEXT,
                    note         TEXT,
                    updated_at   TEXT
                );
            """)

    # ---------- ② 记忆件 WillMemory ----------

    def state_snapshot(self) -> Dict[str, Any]:
        st = self.runtime.state
        return {
            "quadrant": st.pad.dominant_quadrant(),
            "P": round(st.pad.P, 3), "A": round(st.pad.A, 3),
            "D": round(st.pad.D, 3),
            "mood_strength": round(st.pad.mood_strength(), 3),
            "E": st.energy, "F": st.focus,
            "S": st.social_need, "C": round(st.curiosity, 1),
        }

    def state_signature(self) -> str:
        snap = self.state_snapshot()
        return self.signature_from_snapshot(snap)

    @staticmethod
    def signature_from_snapshot(snap: Dict[str, Any]) -> str:
        mood = snap.get("quadrant") or "neutral"

        def level(value: Any, low: int, high: Optional[int] = None) -> str:
            try:
                v = float(value)
            except (TypeError, ValueError):
                return "mid"
            if v < low:
                return "low"
            if high is not None and v >= high:
                return "high"
            return "mid"

        e = level(snap.get("E"), 30, 70)
        s = level(snap.get("S"), 40, 70)
        try:
            c_value = float(snap.get("C"))
        except (TypeError, ValueError):
            c_value = 50.0
        c = "high" if c_value >= 40 else "low"
        return f"{mood}|E-{e}|S-{s}|C-{c}"

    def record_decision(self, action: Dict[str, Any], trigger: str = "idle",
                        mode: str = "rule", confidence: float = 0.6,
                        task_id: Optional[str] = None) -> int:
        """落一条决策，返回 decision_id（失败返回 0，不影响主流程）"""
        try:
            snap = self.state_snapshot()
            with self._lock, sqlite3.connect(self.db_path) as conn:
                cur = conn.execute("""
                    INSERT INTO will_decisions
                    (decided_at, trigger, mode, state_snapshot, action_type,
                     action_target, reasoning, confidence, task_id, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    _now_iso(), trigger, mode,
                    json.dumps(snap, ensure_ascii=False),
                    action.get("type") or "unknown",
                    str(action.get("target") or action.get("tool") or "")[:120],
                    str(action.get("reasoning") or "")[:300],
                    float(confidence), task_id, _now_iso(),
                ))
                return int(cur.lastrowid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[will_soul] 决策落库失败（已忽略）: {e}")
            return 0

    def record_outcome(self, decision_id: int, success: bool,
                       duration_sec: float = 0.0, error: str = "",
                       task_id: Optional[str] = None,
                       reward_delta: float = 0.0, note: str = "") -> bool:
        """回填结果；经验不单独建表，读取时现场聚合"""
        if not decision_id:
            return False
        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO will_outcomes
                    (decision_id, task_id, finished_at, success, duration_sec,
                     error, reward_delta, note)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (decision_id, task_id, _now_iso(), 1 if success else 0,
                      float(duration_sec or 0), (error or "")[:500],
                      float(reward_delta or 0), (note or "")[:200]))
                if task_id:
                    conn.execute("UPDATE will_decisions SET task_id=? WHERE id=?",
                                 (task_id, decision_id))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[will_soul] 结果落库失败（已忽略）: {e}")
            return False

    def recent_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT d.*, o.success, o.finished_at, o.duration_sec,
                       o.error, o.reward_delta
                FROM will_decisions d
                LEFT JOIN will_outcomes o ON o.decision_id = d.id
                ORDER BY d.id DESC LIMIT ?
            """, (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["state"] = json.loads(item.get("state_snapshot") or "{}")
            except Exception:  # noqa: BLE001
                item["state"] = {}
            out.append(item)
        return out

    # ---------- bond：内心侧的关系感 ----------

    def touch_bond(self, user_id: Optional[str], familiarity: float = BOND_FAMILIARITY_STEP,
                   attachment: float = BOND_ATTACHMENT_STEP,
                   when: Optional[str] = None, note: str = "") -> Dict[str, Any]:
        """一次互动后加深熟悉/亲近（按剩余空间缩放，越熟增长越慢）"""
        if not user_id or user_id in ("?", "unknown"):
            return {}
        when = when or _now_iso()
        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM will_bond WHERE user_id=?", (user_id,)
                ).fetchone()
                if row is None:
                    fam = max(0.0, min(1.0, 0.1 + familiarity * (1 - 0.1)))
                    att = max(0.0, min(1.0, 0.05 + attachment * (1 - 0.05)))
                    conn.execute(
                        "INSERT INTO will_bond(user_id, familiarity, attachment,"
                        " interactions, last_seen, note, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (user_id, fam, att, 1, when, note, when))
                else:
                    fam = max(0.0, min(1.0, float(row["familiarity"]) + familiarity * (1 - float(row["familiarity"]))))
                    att = max(0.0, min(1.0, float(row["attachment"]) + attachment * (1 - float(row["attachment"]))))
                    conn.execute(
                        "UPDATE will_bond SET familiarity=?, attachment=?,"
                        " interactions=interactions+1, last_seen=?,"
                        " note=COALESCE(NULLIF(?, ''), note), updated_at=?"
                        " WHERE user_id=?", (fam, att, when, note, when, user_id))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] bond 更新失败: {e}")
        return self.bond_for(user_id)

    def bond_for(self, user_id: Optional[str]) -> Dict[str, Any]:
        if not user_id:
            return {}
        try:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM will_bond WHERE user_id=?", (user_id,)
                ).fetchone()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] bond 读取失败: {e}")
            return {}
        if row is None:
            return {"user_id": user_id, "familiarity": 0.0, "attachment": 0.0,
                    "interactions": 0, "last_seen": None}
        decay = math.exp(-BOND_DECAY_LAMBDA * _days_since(row["last_seen"]))
        return {
            "user_id": user_id,
            "familiarity": round(float(row["familiarity"]) * decay, 3),
            "attachment": round(float(row["attachment"]) * decay, 3),
            "interactions": int(row["interactions"]),
            "last_seen": row["last_seen"],
        }

    def top_bonds(self, limit: int = 3) -> List[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT user_id FROM will_bond").fetchall()
        bonds = [self.bond_for(r[0]) for r in row]
        bonds = [b for b in bonds if b and max(b["familiarity"], b["attachment"]) >= BOND_MIN_VISIBLE]
        bonds.sort(key=lambda b: -(0.5 * b["familiarity"] + 0.5 * b["attachment"]))
        return bonds[:limit]

    def bond_block(self, max_chars: int = 260) -> str:
        bonds = self.top_bonds()
        if not bonds:
            return ""
        lines = ["【我对常驻的人的感觉】"]
        for b in bonds:
            lines.append(
                f"- {b['user_id']}：熟悉 {b['familiarity']:.2f}，"
                f"亲近 {b['attachment']:.2f}，聊过 {b['interactions']} 次")
        return self._clip("\n".join(lines), max_chars)

    # ---------- 作息 routine ----------

    def routine_entries(self) -> List[Dict[str, Any]]:
        raw = self.identity.get("routine") or []
        return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _minutes(hm: Any) -> Optional[int]:
        try:
            h, m = str(hm).split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None

    def is_quiet(self, now: Optional[datetime] = None) -> bool:
        """quiet_hours = ["01:00","07:00"]；支持跨午夜"""
        quiet = self.identity.get("quiet_hours") or []
        if not isinstance(quiet, list) or len(quiet) != 2:
            return False
        start, end = self._minutes(quiet[0]), self._minutes(quiet[1])
        if start is None or end is None:
            return False
        cur = now or datetime.now()
        t = cur.hour * 60 + cur.minute
        if start <= end:
            return start <= t < end
        return t >= start or t < end

    def autonomous_execute(self) -> bool:
        """自主决策是否真的下发执行（profile: will_engine.autonomous_execute）"""
        return bool(self.identity.get("autonomous_execute", True))

    def routine_due(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """当前是否落在某个作息窗口（±5 分钟）"""
        cur = now or datetime.now()
        t = cur.hour * 60 + cur.minute
        for entry in self.routine_entries():
            target = self._minutes(entry.get("time"))
            if target is None:
                continue
            diff = abs(t - target)
            if min(diff, 1440 - diff) <= ROUTINE_WINDOW_MIN:
                key = f"{cur.strftime('%Y-%m-%d')}:{entry.get('time')}:{entry.get('action')}"
                return {"entry": entry, "key": key}
        return None

    def replay_block(self, limit: int = MAX_REPLAY_ROWS,
                     max_chars: int = MAX_REPLAY_CHARS) -> str:
        """启动回放：最近决策 + 结果压成紧凑文本；无历史返回空串"""
        rows = self.recent_decisions(limit)
        if not rows:
            return ""
        lines = ["【我最近的记忆】"]
        for row in rows:
            snap = row.get("state") or {}
            mood = snap.get("quadrant") or "?"
            e = self._level_text(snap.get("E"), 30, 70)
            s = self._level_text(snap.get("S"), 40, 70)
            action = row.get("action_type") or "?"
            hint = _ACTION_HINTS.get(action, "")
            reasoning = str(row.get("reasoning") or "")[:60]
            if row.get("success") is None:
                result = "未完成"
            else:
                result = "成功" if row.get("success") else "失败"
                if row.get("duration_sec"):
                    result += f" {float(row['duration_sec']):.0f}s"
            stamp = self._relative_time(row.get("decided_at"))
            line = f"{stamp} {mood}|E-{e}|S-{s} → {action}"
            if hint:
                line += f"（{hint}）"
            if reasoning:
                line += f"：「{reasoning}」"
            line += f" → {result}"
            lines.append("- " + line[:120])
        return self._clip("\n".join(lines), max_chars)

    # ---------- ③ 经验件 WillExperience（现场聚合） ----------

    def experience_stats(self, signature: Optional[str] = None,
                         limit_rows: int = 300) -> List[Dict[str, Any]]:
        """按当前状态签名聚合历史决策结果，不维护第二张表"""
        target = signature or self.state_signature()
        rows = self.recent_decisions(limit_rows)
        buckets: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            snap = row.get("state") or {}
            if self.signature_from_snapshot(snap) != target:
                continue
            if row.get("success") is None:
                continue
            action = row.get("action_type") or "unknown"
            b = buckets.setdefault(action, {
                "action_type": action, "attempts": 0, "successes": 0,
                "total": 0.0, "last_finished": None,
            })
            success_sign = 1.0 if row.get("success") else -1.0
            reward_norm = max(-1.0, min(1.0, float(row.get("reward_delta") or 0) / 10.0))
            b["attempts"] += 1
            b["successes"] += 1 if row.get("success") else 0
            b["total"] += 0.7 * success_sign + 0.3 * reward_norm
            stamp = row.get("finished_at") or row.get("decided_at")
            if stamp and (b["last_finished"] is None or stamp > b["last_finished"]):
                b["last_finished"] = stamp

        out = []
        for b in buckets.values():
            avg = b["total"] / b["attempts"] if b["attempts"] else 0.0
            weight = math.exp(-DECAY_LAMBDA * _days_since(b["last_finished"]))
            if weight < MIN_EXPERIENCE_WEIGHT:
                continue
            out.append({
                "action_type": b["action_type"],
                "attempts": b["attempts"],
                "successes": b["successes"],
                "avg_outcome": round(avg, 3),
                "weight": round(weight, 3),
                "effective": round(avg * weight, 3),
            })
        out.sort(key=lambda x: (-x["effective"], -x["attempts"]))
        return out

    def experience_block(self, max_chars: int = 450) -> str:
        stats = self.experience_stats()[:MAX_EXPERIENCE_ROWS]
        distilled = self.distilled_map(self.state_signature())
        lines: List[str] = []
        if stats:
            lines.append("【经验（同类心境下）】")
            for s in stats:
                hint = _ACTION_HINTS.get(s["action_type"], "")
                line = (f"- {s['action_type']}"
                        f"{'（' + hint + '）' if hint else ''}："
                        f"{s['attempts']} 次尝试 / {s['successes']} 次成功，"
                        f"平均结果 {s['avg_outcome']:+.2f}")
                note = (distilled.get(s["action_type"]) or {}).get("distilled")
                if note:
                    line += f"｜经验：{note}"
                lines.append(line)

        # 编码类经验复用 learn_procedures，不重复建表
        try:
            learning = getattr(getattr(self.runtime, "self_core", None), "learning", None)
            procs = learning.list_procedures(limit=3) if learning else []
            if procs:
                lines.append("【程序性经验】")
                for p in procs[:3]:
                    if p.get("status") == "retired":
                        continue
                    lines.append(
                        f"- {p['signature']}：可靠性 {p['reliability']:.2f}，"
                        f"使用 {p['uses']} 次")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] 读取程序性经验失败: {e}")

        if not lines:
            return ""
        return self._clip("\n".join(lines), max_chars)

    # ---------- 经验反思（distilled） ----------

    def record_distilled(self, signature: str, action_type: str,
                         distilled: str, attempts: int, rate: float) -> bool:
        """把一句话经验写进 learn_reflections(scope='will')，不另建表"""
        store = getattr(getattr(self.runtime, "self_core", None), "learning", None)
        if store is None or not distilled:
            return False
        try:
            store.log_reflection("will", signature, {
                "signature": signature,
                "action_type": action_type,
                "distilled": distilled[:120],
                "attempts": attempts,
                "rate": round(rate, 3),
            }, applied=True, model="reflect")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[will_soul] 写入 distilled 失败: {e}")
            return False

    def distilled_map(self, signature: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """当前签名下每个动作最近一条 distilled（取最新，按动作去重）"""
        store = getattr(getattr(self.runtime, "self_core", None), "learning", None)
        if store is None:
            return {}
        target = signature or self.state_signature()
        try:
            rows = store.list_reflections(limit=200)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] 读取 distilled 失败: {e}")
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if row.get("scope") != "will":
                continue
            data = row.get("output") or {}
            if data.get("signature") != target:
                continue
            action = data.get("action_type")
            if action and action not in out:
                out[action] = data     # DESC：第一条即最新
        return out

    # ---------- ④ 知识件 WillKnowledge ----------

    def knowledge_block(self, max_chars: int = 500) -> str:
        lines: List[str] = []
        caps = self._capability_labels()
        if caps:
            lines.append("【我现在能做到的】")
            lines.append("- " + "、".join(caps[:12]))

        names = self._interest_names()
        if names:
            lines.append("【我偏好的领域】")
            lines.append("- " + "、".join(names[:8]))

        # 联网研究笔记：让意志脑知道"我最近查过什么"，避免重复研究
        try:
            learning = getattr(getattr(self.runtime, "self_core", None), "learning", None)
            notes = learning.recent_notes(limit=3) if learning else []
            if notes:
                lines.append("【我最近查过】")
                lines.append("- " + "；".join(
                    str(n.get("topic") or "")[:40] for n in notes if n.get("topic")
                ))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] 研究笔记不可用: {e}")

        # 知识缺口：被问到但知识库里没有的东西，供空闲决策"补课"
        try:
            from LLM_v2.core.will_knowledge import get_will_knowledge
            gaps = get_will_knowledge().recent_gaps(limit=3)
            if gaps:
                lines.append("【我还没弄明白的】")
                lines.append("- " + "；".join(
                    str(g.get("topic") or "")[:40]
                    for g in gaps if g.get("topic")))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[will_soul] 知识缺口不可用: {e}")

        if not lines:
            return ""
        return self._clip("\n".join(lines), max_chars)

    def _capability_labels(self) -> List[str]:
        assembly = getattr(self.runtime, "assembly", None)
        if assembly is None:
            return []
        try:
            status = assembly.worlds_status()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[will_soul] 能力清单不可用: {e}")
            return []
        labels = []
        for world in status.get("worlds") or []:
            if not world.get("enabled") or not world.get("tool_count"):
                continue
            label = world.get("label") or world.get("id")
            if label and label not in labels:
                labels.append(str(label))
        return labels

    def _interest_names(self) -> List[str]:
        persona = getattr(self.runtime, "persona", None)
        interests = list(getattr(persona, "interests", None) or [])
        preferred = self.identity.get("preferred_interests") or []
        preferred = [str(x) for x in preferred] if isinstance(preferred, list) else []

        names = []
        for name in preferred:
            if name and name not in names:
                names.append(name)
        rest = sorted(
            [i for i in interests if getattr(i, "name", None) not in names],
            key=lambda i: -float(getattr(i, "enthusiasm", 0.0) or 0.0))
        names.extend(getattr(i, "name", "") for i in rest)
        seen, out = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    # ---------- 决策上下文（8 段） ----------

    def build_decision_context(self, idle_seconds: float = 0.0) -> str:
        """人格 > 状态 > 记忆 > 经验 > 知识 > 任务 > 输出格式"""
        name = getattr(getattr(self.runtime, "persona", None), "display_name", "维尔薇")
        parts: List[str] = [
            f"你是 {name} 的内心（意志脑）。只决定「想做什么」，不产出对外台词。",
            self.identity_block(),
            self.bond_block(),
            self.runtime.state.to_prompt_block(),
            self.replay_block(),
            self.experience_block(),
            self.knowledge_block(),
        ]
        if idle_seconds > 0:
            parts.append(f"你已经空闲了 {idle_seconds:.0f} 秒，根据以上决定下一步想做什么。")
        else:
            parts.append("根据以上决定下一步想做什么。")

        parts.append(
            "可选动作：social_action（找人说话）/ explore_interest（探索兴趣，"
            "可把「我还没弄明白的」当目标去查）/ "
            "watch_video（刷视频）/ code_action（写代码）/ rest（休息）/ diary（写日记）。\n"
            '严格输出 JSON，不要解释、不要 markdown：'
            '{"type": "动作类型", "target": "目标对象", "reasoning": "一句话理由"}'
        )
        # 附加段超出预算时按 记忆 → 经验 → 知识 的顺序截断
        full = "\n\n".join(p for p in parts[:-1] if p)
        tail = parts[-1]
        budget = MAX_CONTEXT_CHARS - len(tail) - 10
        for idx in (4, 5, 6):
            if len(full) <= budget:
                break
            parts[idx] = ""
            full = "\n\n".join(p for p in parts[:-1] if p)
        return (full + "\n\n" + tail).strip()

    # ---------- 导出 ----------

    def export_markdown(self, path: Optional[str] = None) -> str:
        """导出可读的意志脑报告（派生视图，只读）"""
        target = Path(path or (self.profile_path.parent / "will_soul_report.md"))
        decisions = self.recent_decisions(20)
        stats = self.experience_stats()
        lines = [
            "# 意志脑报告",
            "",
            f"生成时间：{_now_iso()}",
            f"当前状态签名：`{self.state_signature()}`",
            "",
            "## 人格（内心）",
            "",
            "```",
            self.identity_block(1200) or "（未配置 will_engine 段）",
            "```",
            "",
            "## 常驻的人（bond）",
            "",
            self.bond_block(800) or "（暂无）",
            "",
            "## 作息（routine）",
            "",
        ]
        routine = self.routine_entries()
        if routine:
            for entry in routine:
                lines.append(f"- {entry.get('time', '?')}　{entry.get('action', '?')}"
                             f"　{entry.get('prompt', '')}")
        else:
            lines.append("（未配置）")
        lines += [
            "",
            "## 最近决策",
            "",
            "| 时间 | 状态 | 动作 | 理由 | 结果 |",
            "|---|---|---|---|---|",
        ]
        for row in decisions:
            snap = row.get("state") or {}
            result = ("未完成" if row.get("success") is None
                      else ("成功" if row.get("success") else "失败"))
            action_cell = row.get("action_type") or ""
            if row.get("action_target"):
                action_cell += f"（{row['action_target']}）"
            lines.append(
                f"| {row.get('decided_at') or ''} | {snap.get('quadrant', '?')} "
                f"| {action_cell} "
                f"| {(row.get('reasoning') or '').replace('|', '/')[:40]} | {result} |")

        lines += ["", "## 经验（当前签名）", ""]
        if stats:
            lines += ["| 动作 | 尝试 | 成功 | 平均结果 | 权重 |", "|---|---|---|---|---|"]
            for s in stats:
                lines.append(f"| {s['action_type']} | {s['attempts']} | "
                             f"{s['successes']} | {s['avg_outcome']:+.2f} | {s['weight']:.2f} |")
        else:
            lines.append("（暂无）")

        lines += ["", "## 能力清单", "", self.knowledge_block(1200) or "（不可用）", ""]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp, target)
        logger.info(f"[will_soul] 报告已导出: {target}")
        return str(target)

    # ---------- 小工具 ----------

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 1].rstrip() + "…"

    @staticmethod
    def _level_text(value: Any, low: int, high: Optional[int] = None) -> str:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "mid"
        if v < low:
            return "low"
        if high is not None and v >= high:
            return "high"
        return "mid"

    @staticmethod
    def _relative_time(stamp: Optional[str]) -> str:
        days = _days_since(stamp)
        if days < 1 / 24:
            return "刚刚"
        if days < 1:
            return f"{int(days * 24)} 小时前"
        if days < 2:
            return "昨天"
        return f"{int(days)} 天前"


__all__ = ["WillSoul"]
