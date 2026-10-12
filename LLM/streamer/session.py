#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话管理：按 (channel, user_id) 维护多轮上下文。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Session:
    channel: str
    user_id: str
    history: List[dict] = field(default_factory=list)   # 该会话的 LLM 消息历史
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()

    def append(self, msg: dict, max_history: int) -> None:
        self.history.append(msg)
        if len(self.history) > max_history:
            self.history = self.history[-max_history:]


class SessionManager:
    """(channel, user_id) → Session；按 max_history 裁剪、按过期时间清理。"""

    def __init__(self, max_history: int = 20, expire_min: float = 60.0):
        self.max_history = max_history
        self.expire_min = expire_min
        self._sessions: Dict[tuple, Session] = {}

    def get(self, channel: str, user_id: str) -> Session:
        key = (channel, user_id)
        s = self._sessions.get(key)
        if s is None:
            s = Session(channel=channel, user_id=user_id)
            self._sessions[key] = s
        s.touch()
        return s

    def append(self, channel: str, user_id: str, msg: dict) -> Session:
        s = self.get(channel, user_id)
        s.append(msg, self.max_history)
        return s

    def drop(self, channel: str, user_id: str) -> None:
        self._sessions.pop((channel, user_id), None)

    def cleanup(self) -> int:
        """清理过期会话，返回移除数。"""
        now = time.time()
        expired = [k for k, s in self._sessions.items()
                   if now - s.last_active > self.expire_min * 60]
        for k in expired:
            self._sessions.pop(k, None)
        if expired:
            logger.info(f"[SessionManager] 清理 {len(expired)} 个过期会话")
        return len(expired)

    def count(self) -> int:
        return len(self._sessions)
