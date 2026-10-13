#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser 工具 Schema（受控 Edge 浏览器，前缀 br_）。"""

from __future__ import annotations

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
    {"type": "function", "function": {"name": "br_navigate",
        "description": "受控 Edge 导航到指定 URL（自动等待加载，返回页面快照）。用户说'访问/打开某个网页'时用。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "目标 URL，需以 http:// 或 https:// 开头"}},
            "required": ["url"]}}},
    {"type": "function", "function": {"name": "br_snapshot",
        "description": "获取当前页面可交互元素列表（带 @eN 引用）+ URL/Title。操作前审视页面、或页面有变化（弹窗等）时调用；每次操作后已自动返回新快照。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "br_get_content",
        "description": "读取当前页面正文文本（前 3000 字），供理解网页/视频页/主页内容。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "br_click",
        "description": "点击页面元素。ref 来自 br_snapshot 的 @eN 引用（如 @e3），点击后自动返回新快照。",
        "parameters": {"type": "object", "properties": {
            "ref": {"type": "string", "description": "元素引用，格式 @eN，如 @e3"}},
            "required": ["ref"]}}},
    {"type": "function", "function": {"name": "br_type",
        "description": "在页面输入框输入文本。ref 来自 br_snapshot；auto_submit=True 时输完自动按 Enter 提交（搜索框推荐）。",
        "parameters": {"type": "object", "properties": {
            "ref": {"type": "string", "description": "输入框引用，如 @e8"},
            "text": {"type": "string", "description": "要输入的文本"},
            "clear_first": {"type": "boolean", "description": "输入前是否清空", "default": True},
            "auto_submit": {"type": "boolean", "description": "输完自动回车提交", "default": False}},
            "required": ["ref", "text"]}}},
    {"type": "function", "function": {"name": "br_press_key",
        "description": "在页面按键：Enter/Tab/Esc/ArrowUp/ArrowDown/Home/End/PageUp/PageDown/F5/Ctrl+R 等。",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "按键名，如 Enter、ArrowDown"}},
            "required": ["key"]}}},
    {"type": "function", "function": {"name": "br_scroll",
        "description": "滚动页面（up/down/left/right，默认下滚 500px）。刷视频/长页面时用。",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
            "amount": {"type": "integer", "description": "滚动像素数", "default": 500}},
            "required": ["direction"]}}},
    {"type": "function", "function": {"name": "br_tabs",
        "description": "标签页管理：list=列出 / new=新开(可带url) / switch=切换(需ref) / close=关闭(需ref)。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "new", "switch", "close"]},
            "ref": {"type": "string", "description": "tab 引用，如 @tab_1（switch/close 需要）"},
            "url": {"type": "string", "description": "new 时初始 URL（可选）"}},
            "required": ["action"]}}},
    {"type": "function", "function": {"name": "br_screenshot",
        "description": "截取当前页面（base64 + 保存 PNG），可交给视觉模型分析页面外观。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "br_wait",
        "description": "等待若干秒（页面加载/动画/网络请求），默认 1 秒。",
        "parameters": {"type": "object", "properties": {
            "seconds": {"type": "number", "default": 1.0}},
            "required": []}}},
    {"type": "function", "function": {"name": "br_stop",
        "description": "关闭受控 Edge 浏览器实例（下次操作自动重启）。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def tools_schema_for(names: list) -> list:
    return [s for s in BROWSER_TOOLS_SCHEMA if s["function"]["name"] in names]
