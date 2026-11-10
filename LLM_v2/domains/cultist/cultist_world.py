# ============================================================
# LLM_v2/domains/cultist/cultist_world.py — 密教模拟器域内信息缓存
# ============================================================
# 这是"能力域内信息层"，只服务 cultist 域：
#   最新局面 + 卡片/动词目录 + 已观察到的配方 + 失败记录 + 学习经验
# 中央信息层（LLM_v2/core/central_context.py）只保存本域的摘要与渠道指针，
# 不复制这里的全量数据。
# ============================================================

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
_STORE = _ROOT / "persona" / "cultist_world.json"

_MAX_RECIPES = 200
_MAX_MOVES = 200
_MAX_LESSONS = 200
_MAX_FAILURES = 100


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: Any, limit: int = 300) -> str:
    return str(text or "").strip()[:limit]


class CultistWorld:
    """cultist 域内信息缓存（线程安全单例）"""

    def __init__(self, path: Path = _STORE):
        self._path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = self._empty()
        self._attached = False       # 缓存是否已打开（域启用且 start 成功）
        self._last_error = ""

    # ---------- 生命周期（与能力域协同）----------

    def open(self) -> None:
        """打开缓存：从磁盘快照恢复；已在打开状态则幂等"""
        with self._lock:
            if self._attached:
                return
            self._data = self._load()
            self._attached = True
            self._last_error = ""

    def close(self, clear_memory: bool = True, purge: bool = False) -> None:
        """关闭缓存：先落盘，再清空内存（默认保留磁盘快照供下次恢复）"""
        with self._lock:
            if self._attached:
                self._save()
            if purge:
                try:
                    self._path.unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    self._last_error = f"purge失败: {e}"
            if clear_memory:
                self._data = self._empty()
            self._attached = False

    def is_open(self) -> bool:
        with self._lock:
            return self._attached

    def info(self) -> Dict[str, Any]:
        """缓存状态信号：供中央信息 / 控制台 / 对接检查使用"""
        with self._lock:
            try:
                size = self._path.stat().st_size if self._path.exists() else 0
            except Exception:  # noqa: BLE001
                size = 0
            entries = self._entry_count_locked() if self._attached else 0
            return {
                "has_cache": True,
                "backend": "json",
                "available": self._attached,
                "has_data": entries > 0,
                "entries": entries,
                "persisted": self._path.exists(),
                "bytes": size,
                "path": str(self._path),
                "updated_at": (self._data.get("updated_at") or "") if self._attached else "",
                "last_error": self._last_error,
            }

    def _ensure_open(self) -> None:
        if not self._attached:
            self.open()

    def _entry_count_locked(self) -> int:
        return sum(len(self._data.get(k) or []) for k in
                   ("recipes", "moves", "failures", "lessons")) \
            + len(self._data.get("cards") or {}) + len(self._data.get("verbs") or {})

    # ---------- 持久化 ----------

    def _empty(self) -> Dict[str, Any]:
        return {
            "updated_at": "",
            "legacy": {},
            "state": {},
            "cards": {},       # element_id -> {label, aspects, description, seen, last_seen}
            "verbs": {},       # verb_id -> {label, slots, seen, last_seen}
            "recipes": [],     # {verb_id, verb_label, recipe, inputs:[element_id], outputs:[...], count, at}
            "moves": [],       # {card_id, card_label, verb_id, slot, ok, error, at}
            "failures": [],    # {signature, error, count, at}
            "lessons": [],     # {topic, text, source, confidence, at}
        }

    def _load(self) -> Dict[str, Any]:
        data = self._empty()
        if not self._path.exists():
            return data
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key, default in data.items():
                    value = loaded.get(key, default)
                    data[key] = value if isinstance(value, type(default)) or default is None else default
        except Exception as e:  # noqa: BLE001
            self._last_error = f"读取失败: {e}"
        return data

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self._path)
            self._last_error = ""
        except Exception as e:  # noqa: BLE001
            self._last_error = f"写入失败: {e}"

    # ---------- 局面 ----------

    def update_state(self, state: Dict[str, Any]) -> None:
        """合并最新局面，同时把见过的卡/动词进目录。"""
        if not isinstance(state, dict):
            return
        self._ensure_open()
        with self._lock:
            self._data["state"] = self._compact_state(state)
            self._data["updated_at"] = _now()
            legacy = state.get("legacy")
            if legacy:
                self._data["legacy"] = {
                    "id": legacy,
                    "label": state.get("legacy_label") or "",
                }
            for card in state.get("cards") or []:
                self._catalog_card(card)
            for verb in state.get("verbs") or []:
                self._catalog_verb(verb)
                for slot in verb.get("slots") or []:
                    for card in slot.get("cards") or []:
                        self._catalog_card(card)
            self._save()

    @staticmethod
    def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """缓存用局面（去掉过长描述，保留决策所需字段）"""
        return {
            "legacy": state.get("legacy"),
            "legacy_label": state.get("legacy_label"),
            "cards": [{
                "id": c.get("id"), "label": c.get("label"),
                "element_id": c.get("element_id"), "quantity": c.get("quantity", 1),
                "aspects": c.get("aspects") or {},
                "description": _clip(c.get("description"), 200),
            } for c in state.get("cards") or []],
            "verbs": [{
                "id": v.get("id"), "verb_id": v.get("verb_id"), "label": v.get("label"),
                "state": v.get("state"), "time_remaining": v.get("time_remaining"),
                "slots": [{
                    "name": s.get("name"), "path": s.get("path"),
                    "cards": [{"id": c.get("id"), "label": c.get("label"),
                               "element_id": c.get("element_id")}
                              for c in s.get("cards") or []],
                } for s in v.get("slots") or []],
                "outputs": [{"label": c.get("label"), "quantity": c.get("quantity", 1)}
                            for o in v.get("outputs") or [] for c in o.get("cards") or []],
            } for v in state.get("verbs") or []],
            "next_event": state.get("next_event"),
        }

    def _catalog_card(self, card: Dict[str, Any]) -> None:
        eid = str(card.get("element_id") or "").strip()
        if not eid:
            return
        entry = self._data["cards"].get(eid) or {"seen": 0}
        entry.update({
            "label": card.get("label") or entry.get("label") or eid,
            "aspects": card.get("aspects") or entry.get("aspects") or {},
            "description": _clip(card.get("description"), 240)
            or entry.get("description", ""),
            "last_seen": _now(),
            "seen": int(entry.get("seen", 0)) + 1,
        })
        self._data["cards"][eid] = entry

    def _catalog_verb(self, verb: Dict[str, Any]) -> None:
        vid = str(verb.get("verb_id") or "").strip()
        if not vid:
            return
        entry = self._data["verbs"].get(vid) or {"seen": 0, "slots": []}
        slots = [s.get("name") for s in verb.get("slots") or [] if s.get("name")]
        if slots:
            entry["slots"] = slots
        entry.update({
            "label": verb.get("label") or entry.get("label") or vid,
            "last_seen": _now(),
            "seen": int(entry.get("seen", 0)) + 1,
        })
        self._data["verbs"][vid] = entry

    # ---------- 观察记录 ----------

    def record_move(self, card: Dict[str, Any], verb: Dict[str, Any],
                    slot: str, ok: bool, error: str = "") -> None:
        self._ensure_open()
        with self._lock:
            self._data["moves"].append({
                "card_id": card.get("id"),
                "card_label": card.get("label"),
                "element_id": card.get("element_id"),
                "verb_id": verb.get("verb_id"),
                "verb_label": verb.get("label"),
                "slot": slot,
                "ok": bool(ok),
                "error": _clip(error, 200),
                "at": _now(),
            })
            self._data["moves"] = self._data["moves"][-_MAX_MOVES:]
            if not ok:
                self._record_failure_locked(f"move:{card.get('element_id')}->{verb.get('verb_id')}:{slot}",
                                            error)
            self._save()

    def record_recipe_started(self, verb: Dict[str, Any], recipe: Dict[str, Any],
                             inputs: List[Dict[str, Any]]) -> None:
        self._ensure_open()
        with self._lock:
            entry = {
                "verb_id": verb.get("verb_id"),
                "verb_label": verb.get("label"),
                "recipe": recipe.get("recipe") or "",
                "inputs": [c.get("element_id") for c in inputs if c.get("element_id")],
                "outputs": [],
                "count": 0,
                "at": _now(),
            }
            self._data["recipes"].append(entry)
            self._data["recipes"] = self._data["recipes"][-_MAX_RECIPES:]
            self._save()

    def record_recipe_outputs(self, verb: Dict[str, Any],
                              outputs: List[Dict[str, Any]]) -> None:
        self._ensure_open()
        with self._lock:
            vid = verb.get("verb_id")
            for entry in reversed(self._data["recipes"]):
                if entry.get("verb_id") == vid and not entry.get("outputs"):
                    entry["outputs"] = [{
                        "label": o.get("label"),
                        "quantity": o.get("quantity", 1),
                    } for o in outputs]
                    entry["count"] = int(entry.get("count", 0)) + 1
                    entry["at"] = _now()
                    break
            self._save()

    def _record_failure_locked(self, signature: str, error: str) -> None:
        rows = self._data["failures"]
        for row in rows:
            if row.get("signature") == signature:
                row["count"] = int(row.get("count", 0)) + 1
                row["error"] = _clip(error, 200)
                row["at"] = _now()
                return
        rows.append({"signature": signature, "error": _clip(error, 200),
                     "count": 1, "at": _now()})
        self._data["failures"] = rows[-_MAX_FAILURES:]

    def record_failure(self, signature: str, error: str = "") -> None:
        self._ensure_open()
        with self._lock:
            self._record_failure_locked(signature, error)
            self._save()

    # ---------- 学习经验 ----------

    def learn(self, topic: str, text: str, source: str = "play",
              confidence: float = 0.6) -> Dict[str, Any]:
        topic = _clip(topic, 80)
        text = _clip(text, 500)
        if not topic or not text:
            return {"success": False, "error": "topic/text 不能为空"}
        self._ensure_open()
        with self._lock:
            for row in self._data["lessons"]:
                if row.get("topic") == topic and row.get("text") == text:
                    row["at"] = _now()
                    row["confidence"] = max(float(row.get("confidence", 0.6)), confidence)
                    self._save()
                    return {"success": True, "updated": True, "topic": topic}
            self._data["lessons"].append({
                "topic": topic, "text": text, "source": source,
                "confidence": round(float(confidence), 2), "at": _now(),
            })
            self._data["lessons"] = self._data["lessons"][-_MAX_LESSONS:]
            self._save()
            return {"success": True, "updated": False, "topic": topic}

    @staticmethod
    def _score(text: str, keywords: List[str]) -> int:
        low = text.lower()
        return sum(1 for kw in keywords if kw and kw.lower() in low)

    def search(self, query: str = "", limit: int = 8) -> Dict[str, Any]:
        """按关键词检索经验/配方/卡牌/动词目录。"""
        self._ensure_open()
        keywords = [k for k in str(query or "").replace("，", " ").replace(",", " ")
                    .split() if k.strip()]
        limit = max(1, min(20, int(limit or 8)))
        with self._lock:
            lessons = self._data["lessons"]
            if keywords:
                scored = sorted(
                    lessons,
                    key=lambda r: (self._score(f"{r.get('topic')} {r.get('text')}", keywords),
                                   r.get("at", "")),
                    reverse=True,
                )
            else:
                scored = list(reversed(lessons))
            recipes = self._data["recipes"]
            if keywords:
                recipes = [r for r in recipes
                           if self._score(
                               f"{r.get('verb_label')} {r.get('recipe')} "
                               f"{' '.join(str(i) for i in r.get('inputs') or [])} "
                               f"{' '.join(str(o.get('label')) for o in r.get('outputs') or [])}",
                               keywords) > 0]
            cards = []
            if keywords:
                for eid, card in self._data["cards"].items():
                    if self._score(f"{eid} {card.get('label')} {card.get('description')}",
                                   keywords) > 0:
                        cards.append({"element_id": eid, **card})
            return {
                "success": True,
                "query": query,
                "lessons": scored[:limit],
                "recipes": list(reversed(recipes))[:limit],
                "cards": cards[:limit],
                "failures": list(reversed(self._data["failures"]))[:limit // 2],
            }

    # ---------- 给决策用的增量信息 ----------

    def _label_for(self, element_id: Any) -> str:
        entry = self._data["cards"].get(str(element_id or "")) or {}
        return str(entry.get("label") or element_id or "")

    def knowledge_block(self, query: str = "", limit: int = 6) -> str:
        found = self.search(query, limit=limit)
        lines: List[str] = []
        for row in found.get("lessons") or []:
            lines.append(f"- [{row.get('source')}] {row.get('topic')}：{row.get('text')}")
        for row in found.get("recipes") or []:
            ins = "、".join(self._label_for(x) for x in row.get("inputs") or []) or "?"
            outs = "、".join(str(o.get("label")) for o in row.get("outputs") or []) or "未知"
            lines.append(f"- 配方：{row.get('verb_label')}（{ins}）→ {outs}"
                         + (f"（{row.get('recipe')}）" if row.get("recipe") else ""))
        for row in found.get("failures") or []:
            lines.append(f"- 失败过 {row.get('count')} 次：{row.get('signature')} "
                         f"→ {row.get('error')}")
        return "\n".join(lines[:limit])

    def recipe_hints(self, state: Dict[str, Any], limit: int = 4) -> List[str]:
        """根据已观察配方，给出当前可用的"卡→动词"建议。"""
        available = {c.get("element_id") for c in state.get("cards") or []}
        hints: List[str] = []
        self._ensure_open()
        with self._lock:
            for verb in state.get("verbs") or []:
                if verb.get("state") != "Unstarted":
                    continue
                for recipe in reversed(self._data["recipes"]):
                    if recipe.get("verb_id") != verb.get("verb_id"):
                        continue
                    inputs = [i for i in recipe.get("inputs") or [] if i]
                    if not inputs or not set(inputs).issubset(available):
                        continue
                    outs = "、".join(str(o.get("label")) for o in recipe.get("outputs") or [])
                    hints.append(
                        f"{verb.get('label')}：把 "
                        f"{'、'.join(self._label_for(i) for i in inputs)} 放进槽位"
                        + (f"，可得到 {outs}" if outs else "")
                    )
                    break
        return hints[:limit]

    # ---------- 对外快照 ----------

    def snapshot(self, include_state: bool = True) -> Dict[str, Any]:
        with self._lock:
            if not self._attached:
                out = {
                    "success": True,
                    "available": False,
                    "cache": self.info(),
                    "updated_at": "",
                    "legacy": {},
                    "counts": {"cards": 0, "verbs": 0, "recipes": 0,
                               "lessons": 0, "failures": 0},
                    "catalog": {"cards": [], "verbs": []},
                    "recipes": [],
                    "lessons": [],
                    "failures": [],
                }
                if include_state:
                    out["state"] = {}
                return out
            out = {
                "success": True,
                "available": True,
                "cache": self.info(),
                "updated_at": self._data.get("updated_at"),
                "legacy": self._data.get("legacy"),
                "counts": {
                    "cards": len(self._data["cards"]),
                    "verbs": len(self._data["verbs"]),
                    "recipes": len(self._data["recipes"]),
                    "lessons": len(self._data["lessons"]),
                    "failures": len(self._data["failures"]),
                },
                "catalog": {
                    "cards": list(self._data["cards"].values())[-20:],
                    "verbs": list(self._data["verbs"].values())[-20:],
                },
                "recipes": self._data["recipes"][-10:],
                "lessons": self._data["lessons"][-15:],
                "failures": self._data["failures"][-10:],
            }
            if include_state:
                out["state"] = self._data.get("state") or {}
            return out


_WORLD: Optional[CultistWorld] = None
_WORLD_LOCK = threading.Lock()


def get_world() -> CultistWorld:
    global _WORLD
    with _WORLD_LOCK:
        if _WORLD is None:
            _WORLD = CultistWorld()
        return _WORLD


__all__ = ["CultistWorld", "get_world"]
