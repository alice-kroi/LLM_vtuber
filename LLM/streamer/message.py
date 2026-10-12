#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准化消息协议（跨渠道唯一契约）。

所有渠道（bili/webui/voice/cli/api）向主播脑投喂该格式；
to_openai_dict() 转旧 LangGraph 链路所需的 OpenAI message 格式（阶段1 兼容）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class StreamerMessage:
    channel: str                     # bili / webui / voice / cli / api
    msg_type: str                    # danmaku / gift / sc / chat / command / question
    user_id: str                     # 用户标识（uid / session_id / cli）
    user_name: str                   # 显示名
    content: str                     # 消息文本
    ts: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> tuple:
        """会话键：同一 (channel, user) 连续对话。"""
        return (self.channel, self.user_id)

    def to_openai_dict(self) -> Dict[str, Any]:
        """转旧 LangGraph 链路格式（OpenAI user message，含 name/source）。"""
        d: Dict[str, Any] = {"role": "user", "content": self.content,
                             "name": self.user_name, "source": self.channel}
        if self.meta.get("important"):
            d["important"] = True
        if "timestamp" in self.meta:
            d["timestamp"] = self.meta["timestamp"]
        return d

    @classmethod
    def from_openai(cls, openai_msg: Dict[str, Any], channel: str = "api",
                    msg_type: str = "chat") -> "StreamerMessage":
        """从旧 OpenAI 格式反向构造（兼容外部 HTTP 注入）。"""
        return cls(
            channel=channel,
            msg_type=msg_type,
            user_id=str(openai_msg.get("name", "匿名")),
            user_name=str(openai_msg.get("name", "匿名")),
            content=str(openai_msg.get("content", "")),
            meta={"important": bool(openai_msg.get("important", False)),
                  "timestamp": openai_msg.get("timestamp", 0)},
        )
