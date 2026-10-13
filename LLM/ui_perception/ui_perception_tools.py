#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_perception 工具 Schema（未知软件界面识别，前缀 uip_）。"""

from __future__ import annotations

UI_PERCEPTION_TOOL_NAMES = [
    "uip_list_windows",
    "uip_dump_ui_tree",
    "uip_click_control",
    "uip_set_text",
    "uip_read_text",
    "uip_ocr_screen",
    "uip_ground_element",
    "uip_capture_window",
]

UI_PERCEPTION_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {"name": "uip_list_windows",
        "description": "列出全部可见窗口（标题/进程/位置）。识别未知软件前先盘点桌面上有哪些窗口。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "uip_dump_ui_tree",
        "description": "[识别界面主力] 用 UI Automation 读窗口的控件树（类型/名称/坐标，含文字控件）。IDE、QQ 等标准应用可据此知道界面有什么按钮/菜单/输入框。无 UIA 暴露的界面会失败，改用 uip_ocr_screen。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词，如 '记事本'、'WPS'"},
            "max_depth": {"type": "integer", "default": 8},
            "max_items": {"type": "integer", "default": 150}},
            "required": ["window_title"]}}},
    {"type": "function", "function": {"name": "uip_click_control",
        "description": "按控件名点击窗口内的元素（优先 UIA 模式，回退坐标点击）。控件名来自 uip_dump_ui_tree。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"},
            "control_name": {"type": "string", "description": "控件名称（可模糊），如 '打开'、'保存'"},
            "index": {"type": "integer", "description": "同名控件第几个，默认 0", "default": 0}},
            "required": ["window_title", "control_name"]}}},
    {"type": "function", "function": {"name": "uip_set_text",
        "description": "给窗口内的输入框设值（优先 UIA ValuePattern，回退剪贴板粘贴，支持中文）。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"},
            "control_name": {"type": "string", "description": "输入框控件名（可模糊）"},
            "text": {"type": "string", "description": "要输入的文本"},
            "index": {"type": "integer", "default": 0}},
            "required": ["window_title", "control_name", "text"]}}},
    {"type": "function", "function": {"name": "uip_read_text",
        "description": "读界面文本：指定控件文本，或整个窗口内全部文字（拼接）。了解窗口内容/状态用。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"},
            "control_name": {"type": "string", "description": "指定控件名（可模糊），留空读整窗文本"}},
            "required": ["window_title"]}}},
    {"type": "function", "function": {"name": "uip_ocr_screen",
        "description": "窗口/全屏截图后 OCR 识别中文文字（返回文字+屏幕坐标）。UIA 读不到的界面（自绘/WebView/游戏）用。需安装 OCR 引擎（pip install paddleocr 或 easyocr）。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词，留空截全屏"}},
            "required": []}}},
    {"type": "function", "function": {"name": "uip_ground_element",
        "description": "视觉定位点击：描述目标（如'登录按钮'），GUI 模型定位坐标并点击。视觉兜底，需配置 Doubao_API_KEY。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词（留空全屏）"},
            "description": {"type": "string", "description": "要点击的目标描述"}},
            "required": ["window_title", "description"]}}},
    {"type": "function", "function": {"name": "uip_capture_window",
        "description": "窗口截图（base64 + 保存 PNG），供视觉模型分析界面外观/布局。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词，留空全屏"}},
            "required": ["window_title"]}}},
]


def tools_schema_for(names: list) -> list:
    return [s for s in UI_PERCEPTION_TOOLS_SCHEMA if s["function"]["name"] in names]
