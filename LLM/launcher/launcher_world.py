#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LauncherWorld：模拟器 + 两款游戏 底层启动能力域（子进程载体，复用 SubprocessSkillWorld）。"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.launcher.launcher_tools import LAUNCHER_TOOL_NAMES, LAUNCHER_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

_LAUNCHER_PROMPT = """可用工具（底层启动，非视觉）：
- launcher_start_all: 一键拉起 MuMu 指定实例 + ALAS + MAA，并在各自主界面点启动
- launcher_mumu: 用 MuMuManager 底层启动指定编号模拟器实例（如 [0,1]）
- launcher_alas / launcher_maa: 底层启动对应应用
- launcher_status: 上报相关窗口是否已存在（判断是否进入应用）
- launcher_stop: 结束本轮编排（不关模拟器/游戏）

执行原则：
1. 一次性完成"启动模拟器+两款+"用 launcher_start_all，不要拆成多次。
2. launcher_status 只在需要确认是否已运行时调用，避免无谓轮询。
3. 点启动靠 Windows 底层控件定位；若某应用按钮控件缺失，返回里会标 need_manual_calibration，交给人工补一次坐标。
4. launcher_* 属于电脑操作类，调用走异步线程池，不阻塞主流程。
"""


class LauncherWorld(SubprocessSkillWorld):
    id = "launcher"
    label = "模拟器与游戏启动域"
    prefix = "launcher_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "launcher_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(LAUNCHER_TOOL_NAMES)

    def tools(self):
        return [s for s in LAUNCHER_TOOLS_SCHEMA if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _LAUNCHER_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info(f"[LauncherWorld] launcher 域已启动并注册代理工具 {self.allowed_names()}")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[LauncherWorld] launcher 域已停止并注销代理工具")


def build_launcher_world() -> LauncherWorld:
    return LauncherWorld()