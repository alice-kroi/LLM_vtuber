#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser 工具 Schema（受控 Edge 浏览器，前缀 br_）。

试点：用声明式 tool_schema() 生成（扩展能力：少写样板、参数与实现一致）。
"""

from __future__ import annotations

from tool.tool_node import tool_schema  # noqa: F401  # 兼容旧导入路径（tool_schema 现迁至 LLM_v2.core.capability_domain）

BROWSER_TOOL_NAMES = [
    "br_navigate",
    "br_snapshot",
    "br_get_content",
    "br_click",
    "br_type",
    "br_press_key",
    "br_scroll",
    "br_tabs",
    "br_screenshot",
    "br_wait",
    "br_stop",
]

BROWSER_TOOLS_SCHEMA: list = [
    tool_schema("br_navigate",
        "受控 Edge 导航到指定 URL（自动等待加载，返回页面快照）。用户说'访问/打开某个网页'时用。",
        {"url": {"type": "string", "description": "目标 URL，需以 http:// 或 https:// 开头"}},
        required=["url"]),
    tool_schema("br_snapshot",
        "获取当前页面可交互元素列表（带 @eN 引用）+ URL/Title。操作前审视页面、或页面有变化（弹窗等）时调用；每次操作后已自动返回新快照。",
        {}),
    tool_schema("br_get_content",
        "读取当前页面正文文本（前 3000 字），供理解网页/视频页/主页内容。",
        {}),
    tool_schema("br_click",
        "点击页面元素。ref 来自 br_snapshot 的 @eN 引用（如 @e3），点击后自动返回新快照。",
        {"ref": {"type": "string", "description": "元素引用，格式 @eN，如 @e3"}},
        required=["ref"]),
    tool_schema("br_type",
        "在页面输入框输入文本。ref 来自 br_snapshot；auto_submit=True 时输完自动按 Enter 提交（搜索框推荐）。",
        {"ref": {"type": "string", "description": "输入框引用，如 @e8"},
         "text": {"type": "string", "description": "要输入的文本"},
         "clear_first": {"type": "boolean", "description": "输入前是否清空", "default": True},
         "auto_submit": {"type": "boolean", "description": "输完自动回车提交", "default": False}},
        required=["ref", "text"]),
    tool_schema("br_press_key",
        "在页面按键：Enter/Tab/Esc/ArrowUp/ArrowDown/Home/End/PageUp/PageDown/F5/Ctrl+R 等。",
        {"key": {"type": "string", "description": "按键名，如 Enter、ArrowDown"}},
        required=["key"]),
    tool_schema("br_scroll",
        "滚动页面（up/down/left/right，默认下滚 500px）。刷视频/长页面时用。",
        {"direction": {"type": "string", "enum": ["up", "down", "left", "right"],
                       "description": "滚动方向", "default": "down"},
         "amount": {"type": "integer", "description": "滚动像素数", "default": 500}},
        required=["direction"]),
    tool_schema("br_tabs",
        "标签页管理：list=列出 / new=新开(可带url) / switch=切换(需ref) / close=关闭(需ref)。",
        {"action": {"type": "string", "enum": ["list", "new", "switch", "close"],
                    "description": "要执行的 tab 操作"},
         "ref": {"type": "string", "description": "tab 引用，如 @tab_1（switch/close 需要）"},
         "url": {"type": "string", "description": "new 时初始 URL（可选）"}},
        required=["action"]),
    tool_schema("br_screenshot",
        "截取当前页面（base64 + 保存 PNG），可交给视觉模型分析页面外观。",
        {}),
    tool_schema("br_wait",
        "等待若干秒（页面加载/动画/网络请求），默认 1 秒。",
        {"seconds": {"type": "number", "description": "等待秒数", "default": 1.0}}),
    tool_schema("br_stop",
        "关闭受控 Edge 浏览器实例（下次操作自动重启）。",
        {}),
]


def tools_schema_for(names: list) -> list:
    return [s for s in BROWSER_TOOLS_SCHEMA if s["function"]["name"] in names]
