#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""launcher 工具 Schema（底层启动模拟器与两款游戏，非视觉）。"""

from __future__ import annotations

LAUNCHER_TOOL_NAMES = [
    "launcher_start_all",
    "launcher_mumu",
    "launcher_alas",
    "launcher_maa",
    "launcher_status",
    "launcher_stop",
]

LAUNCHER_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {
        "name": "launcher_start_all",
        "description": "一键底层启动：拉起 MuMu 指定模拟器实例 + AzurLane Auto Script(ALAS) + MAA，并在各自主界面点击启动按钮开始挂机。底层直接调 exe 与系统指挥，非视觉。返回各步结果。",
        "parameters": {"type": "object", "properties": {
            "instances": {"type": "array", "items": {"type": "integer"},
                          "description": "要启动的模拟器实例编号（默认 [0,1]）"},
            "click_start": {"type": "boolean", "description": "是否在各自主界面点启动按钮（默认 true）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "launcher_mumu",
        "description": "用 MuMuManager 底层启动指定编号的模拟器实例。",
        "parameters": {"type": "object", "properties": {
            "instances": {"type": "array", "items": {"type": "integer"}, "description": "实例编号数组，如 [0,1] 或 [1]"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "launcher_alas",
        "description": "底层启动 AzurLane Auto Script(alas.exe)。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "launcher_maa",
        "description": "底层启动 MAA(maa.exe)。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "launcher_status",
        "description": "上报当前 MuMu/ALAS/MAA 相关窗口在不在，用于确认是否已进入应用。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "launcher_stop",
        "description": "停止 launcher 域本轮编排（保留已启动的应用；不关闭模拟器/游戏）。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
]


def tools_schema_for(names: list) -> list:
    return [s for s in LAUNCHER_TOOLS_SCHEMA if s["function"]["name"] in names]