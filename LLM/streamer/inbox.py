#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播脑统一收件箱。

阶段1：标准化消息统一入口 → 转 OpenAI 格式 → 喂旧 LangGraph 链路
（legacy_deliver 由 main.py 注入，即 process_openai_messages）。LangGraph 并行保留。
阶段2：改为三脑消息链（主播脑门面 ingress）。

双写开关：环境变量 STREAMER_NEW != "0" 时走新链路（阶段2）；默认 "0" 走旧链路。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from LLM.streamer.message import StreamerMessage
from LLM.streamer.session import SessionManager

logger = logging.getLogger(__name__)

LegacyDeliver = Callable[[List[dict]], Awaitable[Any]]


class StreamerInbox:
    """统一收件箱：submit 标准化消息，按开关路由旧/新链路。"""

    def __init__(self, legacy_deliver: Optional[LegacyDeliver] = None):
        self._legacy_deliver = legacy_deliver
        self.sessions = SessionManager()
        self._use_new = os.environ.get("STREAMER_NEW", "0") != "0"

    @property
    def use_new(self) -> bool:
        return self._use_new

    def bind_legacy(self, deliver: LegacyDeliver) -> None:
        """绑定旧链路投递回调（main.py 注入 process_openai_messages）。"""
        self._legacy_deliver = deliver

    async def submit(self, msg: StreamerMessage) -> Dict[str, Any]:
        """渠道消息统一入口。"""
        if self._use_new:
            # 阶段2：三脑消息链（主播脑门面 ingress → 思维脑）
            from LLM.streamer.streamer_brain import streamer_ingress
            return await streamer_ingress(msg, self.sessions)
        # 阶段1：旧链路（LangGraph 并行保留）
        if self._legacy_deliver is None:
            return {"success": False, "error": "inbox 未绑定 legacy_deliver"}
        return await self._legacy_deliver([msg.to_openai_dict()])


# 模块级单例：main.py 初始化时 bind_legacy，webui/api 等渠道用 get_inbox() 提交
_inbox: Optional[StreamerInbox] = None


def init_inbox(legacy_deliver: Optional[LegacyDeliver] = None) -> StreamerInbox:
    global _inbox
    if _inbox is None:
        _inbox = StreamerInbox()
    if legacy_deliver is not None:
        _inbox.bind_legacy(legacy_deliver)
    return _inbox


def get_inbox() -> Optional[StreamerInbox]:
    return _inbox
