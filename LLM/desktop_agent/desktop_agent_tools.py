#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desktop_agent 域工具 Schema（纯底层桌面感知与操作）。"""

DESKTOP_AGENT_TOOL_NAMES = [
    "dsk_available_apps",
    "dsk_foreground_window",
    "dsk_windows",
    "dsk_clickable",
    "dsk_click",
    "dsk_activate",
]

DESKTOP_AGENT_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {
        "name": "dsk_available_apps",
        "description": "列出桌面可启动软件：桌面与开始菜单快捷方式（可选含注册表已安装应用）。返回名称/路径/类别。",
        "parameters": {"type": "object", "properties": {
            "include_registry": {"type": "boolean",
                                 "description": "是否补充注册表已安装应用（默认 false）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dsk_foreground_window",
        "description": "返回当前前台窗口：标题/进程/pid/矩形坐标。判断用户当前在用什么程序。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dsk_windows",
        "description": "列出当前所有可见顶层窗口：标题/进程/pid/矩形。确认桌面上有哪些前台程序在使用。",
        "parameters": {"type": "object", "properties": {
            "visible_only": {"type": "boolean", "description": "是否只列可见窗口（默认 true）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dsk_clickable",
        "description": "列出指定窗口内可点击的控件（UIA：按钮/链接/菜单项/输入框等），含屏幕绝对坐标与文本。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "窗口标题关键词（与 hwnd 二选一）"},
            "hwnd": {"type": "integer", "description": "窗口句柄（与 title_keyword 二选一）"},
            "max_items": {"type": "integer", "description": "最多返回控件数（默认 80）"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dsk_click",
        "description": "点击屏幕绝对坐标 (x,y)。background_hwnd>0 时用 PostMessage 后台点击不抢焦点。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer", "description": "屏幕绝对 X"},
            "y": {"type": "integer", "description": "屏幕绝对 Y"},
            "button": {"type": "string", "enum": ["left", "right"], "description": "按键（默认 left）"},
            "background_hwnd": {"type": "integer", "description": "后台点击的目标窗口句柄（默认 0=前台）"},
        }, "required": ["x", "y"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dsk_activate",
        "description": "把指定窗口带到前台（还原最小化），便于后续操作。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "窗口标题关键词"},
            "hwnd": {"type": "integer", "description": "窗口句柄（与 title_keyword 二选一）"},
        }, "additionalProperties": False},
    }},
]
