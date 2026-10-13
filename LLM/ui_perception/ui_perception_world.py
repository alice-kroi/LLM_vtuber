#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UiPerceptionWorld：未知软件界面识别能力域（UIA 树 → OCR → 视觉 grounding）。

命名遵循自动发现约定：LLM/<domain>/<domain>_world.py + build_<domain>_world()。
"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.ui_perception.ui_perception_tools import (
    UI_PERCEPTION_TOOL_NAMES, UI_PERCEPTION_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)

_UI_PERCEPTION_PROMPT = """可用工具（未知软件界面识别，分级）：
- uip_list_windows: 盘点可见窗口
- uip_dump_ui_tree: UIA 读控件树（主力，IDE/QQ 等标准界面）
- uip_click_control / uip_set_text / uip_read_text: 按控件名点击/输入/读文本
- uip_ocr_screen: 截图 OCR（自绘/无 UIA 界面兜底，需装 OCR 引擎）
- uip_ground_element: 视觉定位点击（视觉兜底，需 Doubao_API_KEY）
- uip_capture_window: 窗口截图供视觉模型分析

执行原则：
1. 先 uip_list_windows 找到目标窗口，再 uip_dump_ui_tree 读界面。
2. 点击/输入优先 uip_click_control / uip_set_text（UIA 精确）；读内容用 uip_read_text。
3. UIA 读不到（自绘/WebView）→ uip_ocr_screen 拿文字坐标；仍不行 → uip_ground_element。
4. 操作后 uip_read_text / uip_capture_window 确认结果，不成立就修正重试。
"""


class UiPerceptionWorld(SubprocessSkillWorld):
    id = "ui_perception"
    label = "未知软件界面识别（UI Perception）"
    prefix = "uip_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "ui_perception_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(UI_PERCEPTION_TOOL_NAMES)

    def tools(self):
        return [s for s in UI_PERCEPTION_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _UI_PERCEPTION_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info("[UiPerceptionWorld] ui_perception 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[UiPerceptionWorld] ui_perception 域已停止并注销代理工具")


def build_ui_perception_world() -> UiPerceptionWorld:
    return UiPerceptionWorld()
