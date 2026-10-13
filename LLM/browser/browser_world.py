#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BrowserWorld：浏览器控制能力域（受控 Edge，子进程隔离）。

命名遵循自动发现约定：LLM/<domain>/<domain>_world.py + build_<domain>_world()。
"""

from __future__ import annotations

import logging
import os

from tool.tool_node import tool_registry
from LLM.skill_world import SubprocessSkillWorld
from LLM.browser.browser_tools import BROWSER_TOOL_NAMES, BROWSER_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

_BROWSER_PROMPT = """可用工具（受控 Edge 浏览器，DOM 级操作）：
- br_navigate: 访问网页（自动等待加载）
- br_snapshot: 读页面可交互元素（带 @eN 引用）
- br_get_content: 读页面正文文本（理解网页/视频页/主页内容）
- br_click / br_type / br_press_key / br_scroll: 点击/输入/按键/滚动（ref 来自 snapshot）
- br_tabs: 标签页管理（list/new/switch/close）
- br_screenshot: 页面截图（可交视觉模型分析）
- br_wait: 等待；br_stop: 关闭浏览器

执行原则：
1. 先 br_navigate 到目标页，再用 br_snapshot / br_get_content 了解页面。
2. 点击/输入用 snapshot 返回的 @eN 引用，不要猜坐标。
3. 搜索框用 br_type + auto_submit=True（自动回车提交）。
4. 需要看视频/滚动长页用 br_scroll；管理主页/登录态复用同一浏览器实例。
5. 关键操作后用 br_snapshot / br_get_content 确认结果，不成立就修正重试。
"""


class BrowserWorld(SubprocessSkillWorld):
    id = "browser"
    label = "浏览器控制（Edge）"
    prefix = "br_"
    default_enabled = False

    def _engine_path(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "browser_engine.py")

    def launch_args(self):
        return [self._engine_path()]

    def allowed_names(self):
        return list(BROWSER_TOOL_NAMES)

    def tools(self):
        return [s for s in BROWSER_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]

    def system_prompt_fragment(self):
        return _BROWSER_PROMPT

    async def start(self):
        await super().start()
        for name in self.allowed_names():
            if tool_registry.get_tool(name) is None:
                tool_registry.register_tool(name, self._proxy_tool(name))
        logger.info("[BrowserWorld] browser 域已启动并注册代理工具")

    async def stop(self):
        await super().stop()
        for name in self.allowed_names():
            if name in tool_registry.tools:
                tool_registry.tools.pop(name, None)
        logger.info("[BrowserWorld] browser 域已停止并注销代理工具")


def build_browser_world() -> BrowserWorld:
    return BrowserWorld()
