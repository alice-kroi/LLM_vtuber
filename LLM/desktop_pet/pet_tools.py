#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desktop_pet 能力域工具 Schema。"""

DESKTOP_PET_TOOL_NAMES = [
    "pet_start",
    "pet_stop",
    "pet_status",
]

DESKTOP_PET_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {
        "name": "pet_start",
        "description": "启动桌面宠物（Bongo Cat Mver，F:\\爱丽）与悬浮输入框。输入框输入内容会发送给主播脑。",
        "parameters": {"type": "object", "properties": {
            "with_input": {"type": "boolean", "description": "是否同时启动输入框（默认 true）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pet_stop",
        "description": "结束桌面宠物与输入框进程。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pet_status",
        "description": "桌宠与输入框的运行状态。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
]
