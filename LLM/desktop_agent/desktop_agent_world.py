#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DesktopAgentWorld：桌面内容感知与操作能力域（纯底层，非视觉）。"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.desktop_agent.desktop_agent_tools import (
    DESKTOP_AGENT_TOOL_NAMES, DESKTOP_AGENT_TOOLS_SCHEMA)

logger = logging.getLogger(__name__)

_DESKTOP_AGENT_PROMPT = """可用工具：
- dsk_available_apps: 列出可启动软件（桌面/开始菜单快捷方式，可含注册表）
- dsk_foreground_window: 当前前台窗口（标题/进程/pid/矩形）
- dsk_windows: 所有可见顶层窗口（判断桌面上有哪些前台程序在使用）
- dsk_clickable: 列出窗口内可点击控件（UIA：按钮/链接/菜单/输入框 + 绝对坐标）
- dsk_click: 点击屏幕绝对坐标（可后台 PostMessage）
- dsk_activate: 把窗口带到前台

执行原则：
1. 先 dsk_windows / dsk_foreground_window 盘点桌面现状，再决定操作目标。
2. 要启动软件用 dsk_available_apps 找路径，走 os.startfile / launcher 域启动。
3. 要点界面用 dsk_clickable 拿控件文本+坐标 → dsk_click（纯底层，不用截图）。
4. 全程系统 API / UIA，非视觉识别。
"""


class DesktopAgentWorld(SubprocessSkillWorld):
    id = "desktop_agent"
    label = "桌面内容感知与操作"
    prefix = "dsk_"
    default_enabled = True

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "desktop_agent_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(DESKTOP_AGENT_TOOL_NAMES)

    def tools(self):
        return [s for s in DESKTOP_AGENT_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _DESKTOP_AGENT_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info(f"[DesktopAgentWorld] desktop_agent 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[DesktopAgentWorld] desktop_agent 域已停止并注销代理工具")


def build_desktop_agent_world() -> DesktopAgentWorld:
    return DesktopAgentWorld()
