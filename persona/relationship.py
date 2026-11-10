# ============================================================
# persona/relationship.py — 关系变量
# ============================================================
# 小鲤对每个观众/用户的关系是分开的：同一声夸奖，熟人和陌生人效果不同。
#   两个维度（刻意只留两个，避免变成"好感度打怪升级"）：
#     trust     信任：对方是否靠谱、是否值得交付任务
#     intimacy  亲密：互动频率与情感距离
#   affinity = 0.6*trust + 0.4*intimacy，用于调制情绪强度（0~1）
#
# 存储：persona/relationships.json（人可读、原子写入、无需改 SQLite schema）
# ============================================================

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_TRUST = 0.20
DEFAULT_INTIMACY = 0.05
# 熟人门槛：affinity 达到该值后，同一事件的刺激会被放大
ACQUAINTANCE_AT = 0.35


def _default_record() -> Dict[str, Any]:
    return {"trust": DEFAULT_TRUST, "intimacy": DEFAULT_INTIMACY,
            "interactions": 0, "last_seen": None}


def affinity(rec: Dict[str, Any]) -> float:
    return round(0.6 * rec.get("trust", 0.0) + 0.4 * rec.get("intimacy", 0.0), 3)


class RelationshipStore:
    """按用户维护的关系表（JSON 持久化）"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {k: {**_default_record(), **v}
                              for k, v in raw.items() if isinstance(v, dict)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[relationship] 读取失败，按空表启动: {e}")

    def _flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[relationship] 写入失败: {e}")

    def get(self, user_id: str) -> Dict[str, Any]:
        rec = dict(self._data.get(user_id) or _default_record())
        rec["affinity"] = affinity(rec)
        rec["acquaintance"] = rec["affinity"] >= ACQUAINTANCE_AT
        return rec

    def touch(self, user_id: str, *, trust: float = 0.0,
              intimacy: float = 0.0, when: str | None = None) -> Dict[str, Any]:
        """一次互动后更新关系。增量按剩余空间缩放，越熟越难再涨。"""
        if not user_id:
            return self.get(user_id)
        with self._lock:
            rec = self._data.setdefault(user_id, _default_record())
            if trust:
                rec["trust"] = max(0.0, min(1.0, rec["trust"] + trust * (1 - rec["trust"])))
            if intimacy:
                rec["intimacy"] = max(0.0, min(1.0,
                                                  rec["intimacy"] + intimacy * (1 - rec["intimacy"])))
            rec["interactions"] = rec.get("interactions", 0) + 1
            if when:
                rec["last_seen"] = when
            self._flush()
        return self.get(user_id)

    def top(self, n: int = 5) -> List[Dict[str, Any]]:
        rows = [{"user_id": uid, **self.get(uid)} for uid in self._data]
        rows.sort(key=lambda r: -r["affinity"])
        return rows[:n]

    def __len__(self) -> int:
        return len(self._data)


__all__ = ["RelationshipStore", "affinity", "ACQUAINTANCE_AT"]
