#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_perception 能力域：未知软件界面识别（UIA 树 → OCR → 视觉 grounding）。

- ui_perception.py        核心库（UIA dump/点击/设值/读文本、OCR、视觉定位、截图）
- ui_perception_tools.py  工具 Schema（uip_ 前缀）
- ui_perception_engine.py 子进程引擎（JSON 行协议）
- ui_perception_world.py  UiPerceptionWorld（SkillWorld，自动发现挂载）
"""

from LLM.ui_perception.ui_perception_world import build_ui_perception_world

__all__ = ["build_ui_perception_world"]
