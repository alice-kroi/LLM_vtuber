#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser 能力域：受控 Edge 浏览器控制（DOM 级）。

- browser.py        核心库（导航/快照/正文/点击/输入/按键/滚动/标签/截图/等待）
- browser_tools.py  工具 Schema（br_ 前缀）
- browser_engine.py 子进程引擎（JSON 行协议，单事件循环跑 Playwright）
- browser_world.py  BrowserWorld（SkillWorld，自动发现挂载）
"""

from LLM.browser.browser_world import build_browser_world

__all__ = ["build_browser_world"]
