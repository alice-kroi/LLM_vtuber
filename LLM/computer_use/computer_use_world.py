#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ComputerUseWorld：电脑常规操作能力域（纯底层，子进程隔离）。

命名遵循自动发现约定：LLM/<domain>/<domain>_world.py + build_<domain>_world()。
"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.computer_use.computer_use_tools import (
    COMPUTER_USE_TOOL_NAMES, COMPUTER_USE_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)

_COMPUTER_USE_PROMPT = """可用工具（电脑常规操作，纯底层）：
- 文件/目录：cu_find_file（查找）、cu_list_directory（列目录）、cu_open_folder（开文件夹）、cu_open_file（开文件）
- 软件：cu_list_apps（列出可启动软件）、cu_launch_app（启动软件）
- 键鼠：cu_type_text（输入，支持中文）、cu_press_key（按键）、cu_click_xy（点击坐标）
- 剪贴板：cu_clipboard_get / cu_clipboard_set
- 进程/窗口：cu_list_processes、cu_list_windows、cu_foreground、cu_minimize_window、cu_restore_window、cu_show_desktop
- 验证：cu_check（断言核验）

执行原则：
1. 先盘点再动手：先 cu_list_windows 看桌面遮挡/可用窗口，cu_list_processes 看软件是否在跑。
2. 找文件用 cu_find_file，读目录用 cu_list_directory，不要全靠截图猜。
3. 开文件夹/启动软件优先 cu_open_folder / cu_launch_app（系统 Shell 最稳），别模拟 win+r。
4. 目标被遮挡/找不到 → cu_minimize_window 或 cu_show_desktop 清理遮挡后重试。
5. 关键步骤用 cu_check 验证结果（窗口/文件/目录/控件断言），不成立就修正重试，绝不假装成功。
"""


class ComputerUseWorld(SubprocessSkillWorld):
    id = "computer_use"
    label = "电脑常规操作（Computer Use）"
    prefix = "cu_"
    default_enabled = False
    # 工具级超时覆盖（父侧等待上限）：全盘搜文件可能很慢，单独放宽
    tool_timeouts = {"cu_find_file": 180}

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "computer_use_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(COMPUTER_USE_TOOL_NAMES)

    def tools(self):
        return [s for s in COMPUTER_USE_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _COMPUTER_USE_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info("[ComputerUseWorld] computer_use 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[ComputerUseWorld] computer_use 域已停止并注销代理工具")


def build_computer_use_world() -> ComputerUseWorld:
    return ComputerUseWorld()
