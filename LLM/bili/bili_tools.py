#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili 能力域工具 Schema。"""

BILI_TOOL_NAMES = [
    "bili_start",
    "bili_stop",
    "bili_status",
    "bili_observe",
]

BILI_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {
        "name": "bili_start",
        "description": "启动 B 站直播间弹幕/礼物/SC 监听（blivedm，自动重连）。room_ids 缺省取配置。",
        "parameters": {"type": "object", "properties": {
            "room_ids": {"type": "array", "items": {"type": "integer"},
                         "description": "直播间号列表（缺省用 skill_worlds.json / config.json）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "bili_stop",
        "description": "停止 B 站监听。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "bili_status",
        "description": "监听状态：是否运行/房间/转发目标/最近错误/缓冲大小。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "bili_observe",
        "description": "查看最近收到的弹幕/礼物/SC 消息流（缓冲窗口），了解直播间当前动态。",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回最近 N 条（默认 20）"},
        }, "additionalProperties": False},
    }},
]
