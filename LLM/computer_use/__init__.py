#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""computer_use 能力域：电脑常规操作（纯底层，非视觉）。

- computer_use.py        核心库（文件/目录/软件/键鼠/剪贴板/进程/窗口）
- computer_use_tools.py  工具 Schema（cu_ 前缀）
- computer_use_engine.py 子进程引擎（JSON 行协议）
- computer_use_world.py  ComputerUseWorld（SkillWorld，自动发现挂载）
"""

from LLM.computer_use.computer_use_world import build_computer_use_world

__all__ = ["build_computer_use_world"]
