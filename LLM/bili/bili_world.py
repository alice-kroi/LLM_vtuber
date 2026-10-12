#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BiliWorld：B 站直播监控能力域（阶段0：仍走旧 HTTP 回流，主播脑不动）。"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.bili.bili_tools import BILI_TOOL_NAMES, BILI_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

_BILI_PROMPT = """可用工具：
- bili_start: 启动 B 站直播间弹幕/礼物/SC 监听（自动重连）
- bili_stop: 停止监听
- bili_status: 监听状态（房间/运行/最近错误）
- bili_observe: 查看最近弹幕/礼物/SC 消息流

执行原则：
1. 直播间消息已转发给主播脑处理；bili_observe 用于查看最近动态。
2. 需要查看直播间状态先 bili_status，未运行再 bili_start。
"""


class BiliWorld(SubprocessSkillWorld):
    id = "bili"
    label = "B 站直播监控"
    prefix = "bili_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "bili_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(BILI_TOOL_NAMES)

    def tools(self):
        return [s for s in BILI_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _BILI_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info("[BiliWorld] bili 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[BiliWorld] bili 域已停止并注销代理工具")


def build_bili_world() -> BiliWorld:
    return BiliWorld()
