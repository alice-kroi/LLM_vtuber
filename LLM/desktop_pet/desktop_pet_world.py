#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DesktopPetWorld：桌面宠物 + 输入框能力域。

命名遵循自动发现约定：LLM/<domain>/<domain>_world.py + build_<domain>_world()。
"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.desktop_pet.pet_tools import DESKTOP_PET_TOOL_NAMES, DESKTOP_PET_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

_PET_PROMPT = """可用工具：
- pet_start: 启动桌面宠物（Bongo Cat）+ 悬浮输入框（输入内容会发给主播脑）
- pet_stop: 停止桌宠与输入框
- pet_status: 查看运行状态

执行原则：
1. 用户要求"打开桌宠/桌面宠物/宠物"时用 pet_start。
2. 输入框输入的内容经 /api/send 投主播脑 inbox，主播脑会处理回复。
"""


class DesktopPetWorld(SubprocessSkillWorld):
    id = "desktop_pet"
    label = "桌面宠物与输入框"
    prefix = "pet_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "pet_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(DESKTOP_PET_TOOL_NAMES)

    def tools(self):
        return [s for s in DESKTOP_PET_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _PET_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info("[DesktopPetWorld] desktop_pet 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[DesktopPetWorld] desktop_pet 域已停止并注销代理工具")


def build_desktop_pet_world() -> DesktopPetWorld:
    return DesktopPetWorld()
