# ============================================================
# LLM_v2/domains/stream_assist/stream_assist.py — 直播互动助手
# ============================================================
# 常见 AI 主播能力里，本项目缺的是"直播现场运营"：
#   高光标记（供剪辑）、点歌台、观众签到。
# 全部本地 JSON，无网络、无额外依赖，风险 R1。
# ============================================================

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.stream_assist.stream_assist_tools import (
    STREAM_ASSIST_TOOL_NAMES,
    STREAM_ASSIST_TOOLS_SCHEMA,
)

_ROOT = Path(__file__).resolve().parents[3]
_STORE = _ROOT / "persona" / "stream_assist.json"
_MAX_HIGHLIGHTS = 500
_MAX_QUEUE = 200


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class StreamAssistDomain(Actuator):
    """直播互动助手（Actuator）"""

    id = "stream_assist"
    label = "直播互动助手"
    prefix = "sa_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 30.0

    def __init__(self):
        super().__init__()
        self._path = _STORE

    def allowed_names(self) -> List[str]:
        return list(STREAM_ASSIST_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in STREAM_ASSIST_TOOLS_SCHEMA
                if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return ("【直播互动助手】高光标记 / 点歌队列 / 观众签到。"
                "相关数据存 persona/stream_assist.json。")

    # ---------- 存储 ----------

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"highlights": [], "queue": [], "checkins": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"highlights": [], "queue": [], "checkins": {}}
        data.setdefault("highlights", [])
        data.setdefault("queue", [])
        data.setdefault("checkins", {})
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self._path)

    # ---------- 工具 ----------

    def sa_mark_highlight(self, note: str = "", source: str = "manual") -> Dict[str, Any]:
        note = (note or "").strip()
        if not note:
            return {"success": False, "error": "note 不能为空"}
        data = self._load()
        entry = {"at": _now(), "note": note[:200], "source": source or "manual"}
        data["highlights"].append(entry)
        data["highlights"] = data["highlights"][-_MAX_HIGHLIGHTS:]
        self._save(data)
        return {"success": True, "count": len(data["highlights"]), "entry": entry}

    def sa_list_highlights(self, limit: int = 10) -> Dict[str, Any]:
        data = self._load()
        rows = data["highlights"][-max(1, int(limit or 10)):][::-1]
        return {"success": True, "count": len(rows), "highlights": rows}

    def sa_queue_add(self, song: str = "", user: str = "") -> Dict[str, Any]:
        song = (song or "").strip()
        if not song:
            return {"success": False, "error": "song 不能为空"}
        data = self._load()
        entry = {"at": _now(), "song": song[:80], "user": (user or "")[:60]}
        data["queue"].append(entry)
        data["queue"] = data["queue"][-_MAX_QUEUE:]
        self._save(data)
        return {"success": True, "queue_size": len(data["queue"]), "entry": entry}

    def sa_queue_list(self, limit: int = 20) -> Dict[str, Any]:
        data = self._load()
        queue = data["queue"][:max(1, int(limit or 20))]
        return {"success": True, "queue_size": len(data["queue"]), "queue": queue}

    def sa_queue_pop(self) -> Dict[str, Any]:
        data = self._load()
        if not data["queue"]:
            return {"success": False, "error": "点歌队列为空"}
        entry = data["queue"].pop(0)
        self._save(data)
        return {"success": True, "song": entry.get("song", ""), "entry": entry,
                "queue_size": len(data["queue"])}

    def sa_checkin(self, user: str = "") -> Dict[str, Any]:
        user = (user or "").strip() or "anonymous"
        data = self._load()
        rec = data["checkins"].get(user) or {"last_date": "", "streak": 0, "total": 0}
        today = _today()
        if rec.get("last_date") == today:
            return {"success": True, "already": True, "user": user,
                    "streak": int(rec.get("streak", 0)),
                    "total": int(rec.get("total", 0))}

        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        streak = int(rec.get("streak", 0)) + 1 if rec.get("last_date") == yesterday else 1
        total = int(rec.get("total", 0)) + 1
        data["checkins"][user] = {"last_date": today, "streak": streak, "total": total}
        self._save(data)
        return {"success": True, "already": False, "user": user,
                "streak": streak, "total": total, "date": today}


def build_stream_assist_domain() -> StreamAssistDomain:
    return StreamAssistDomain()


__all__ = ["StreamAssistDomain", "build_stream_assist_domain"]
