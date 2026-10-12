#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渠道抽象：统一对外收发的接口。

阶段1 只落骨架：Channel 定义 ingress/egress 契约 + CLI/WebUI 内置渠道；
egress 主要由阶段2 三脑链路使用（旧 LangGraph 阶段1 仍自行广播回复）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from LLM.streamer.message import StreamerMessage
    from LLM.streamer.session import Session

logger = logging.getLogger(__name__)


class Channel(ABC):
    name: str = ""

    @abstractmethod
    async def egress(self, reply: str, sess: Optional["Session"] = None) -> None:
        """把回复发送到该渠道（B站发弹幕 / WebUI 显示 / CLI 打印…）。"""


class CliChannel(Channel):
    name = "cli"

    async def egress(self, reply: str, sess: Optional["Session"] = None) -> None:
        print(f"[主播] {reply}")


class WebuiChannel(Channel):
    name = "webui"

    async def egress(self, reply: str, sess: Optional["Session"] = None) -> None:
        try:
            from webui.app import ws_manager
            await ws_manager.broadcast("reply", {"content": reply,
                                                 "to": sess.user_id if sess else ""},
                                       "chat")
        except Exception as e:
            logger.warning(f"WebUI egress 失败: {e}")


class BiliChannel(Channel):
    """bili 渠道 egress：经 bili 域 bili_send 发弹幕（阶段2 接）。"""

    name = "bili"

    async def egress(self, reply: str, sess: Optional["Session"] = None) -> None:
        # 阶段2：调 bili 域 bili_send；阶段1 暂不主动发（旧链路已在播报）
        logger.info(f"[bili-egress]（阶段2 启用发送）{reply[:40]}")


# 内置渠道注册表
CHANNELS: dict[str, Channel] = {
    "cli": CliChannel(),
    "webui": WebuiChannel(),
    "bili": BiliChannel(),
}
