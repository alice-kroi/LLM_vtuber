#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""computer_use 工具 Schema（纯底层电脑常规操作，前缀 cu_）。"""

from __future__ import annotations

COMPUTER_USE_TOOL_NAMES = [
    "cu_find_file",
    "cu_open_folder",
    "cu_open_file",
    "cu_launch_app",
    "cu_list_apps",
    "cu_list_directory",
    "cu_type_text",
    "cu_press_key",
    "cu_click_xy",
    "cu_clipboard_get",
    "cu_clipboard_set",
    "cu_list_processes",
    "cu_list_windows",
    "cu_foreground",
    "cu_minimize_window",
    "cu_restore_window",
    "cu_show_desktop",
    "cu_check",
]

COMPUTER_USE_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {"name": "cu_find_file",
        "description": "按文件名关键词递归搜索文件（默认 C:\\ 全盘，跳过 node_modules/.git/Windows 等）。用户问'XX 文件在哪里'时用；可指定 root 和 depth 缩小范围。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "文件名关键词，如 'test'、'计划'"},
            "root": {"type": "string", "description": "搜索根目录，默认 C:\\\\", "default": "C:\\\\"},
            "depth": {"type": "integer", "description": "递归深度上限，默认 6", "default": 6},
            "limit": {"type": "integer", "description": "结果上限，默认 20", "default": 20}},
            "required": ["keyword"]}}},
    {"type": "function", "function": {"name": "cu_open_folder",
        "description": "打开文件夹/盘符/我的电脑/下载等（系统 Shell 最稳）。用户说'打开E盘''打开XX文件夹'时用。",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "目标：'E:\\'、'E:\\GitHub'、'我的电脑'、'下载'、'桌面' 等"}},
            "required": ["target"]}}},
    {"type": "function", "function": {"name": "cu_open_file",
        "description": "用系统默认程序打开任意文件（文档/图片/程序）。用户说'打开 XXX 文件'时用。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件完整路径，如 'E:\\notes\\计划.txt'"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "cu_launch_app",
        "description": "启动软件：可给 exe 路径，或应用名/开始菜单快捷方式名（如 notepad、WPS、QQ）。用户说'打开XX软件/启动XX程序'时用。",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "应用 exe 路径或应用名，如 'C:\\Windows\\notepad.exe'、'notepad'、'WPS'"}},
            "required": ["app"]}}},
    {"type": "function", "function": {"name": "cu_list_apps",
        "description": "列出本机可启动软件（桌面/开始菜单快捷方式，可选注册表已安装）。不知道软件准确名字时先列出再启动。",
        "parameters": {"type": "object", "properties": {
            "include_registry": {"type": "boolean", "default": False,
                                 "description": "是否包含注册表已安装应用（更全但更多）"}},
            "required": []}}},
    {"type": "function", "function": {"name": "cu_list_directory",
        "description": "读取指定目录下的文件夹/文件列表（用户问'XX盘/文件夹里有什么'时用，直接读文件系统最准）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径，如 'E:\\' 或 'E:\\GitHub'"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "cu_type_text",
        "description": "在当前聚焦输入框输入文本（支持中文，走剪贴板粘贴）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "cu_press_key",
        "description": "按组合键或单键，如 'ctrl+s'、'alt+tab'、'enter'、'f5'。",
        "parameters": {"type": "object", "properties": {
            "hotkey": {"type": "string", "description": "按键或组合键，如 ctrl+s"}},
            "required": ["hotkey"]}}},
    {"type": "function", "function": {"name": "cu_click_xy",
        "description": "在屏幕绝对像素坐标点击鼠标。坐标来自视觉定位或已知位置。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "clicks": {"type": "integer", "default": 1},
            "button": {"type": "string", "default": "left"}},
            "required": ["x", "y"]}}},
    {"type": "function", "function": {"name": "cu_clipboard_get",
        "description": "读取剪贴板当前文本内容。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "cu_clipboard_set",
        "description": "把文本写入剪贴板（配合粘贴使用）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "cu_list_processes",
        "description": "列出当前进程（可选按名称关键词过滤，如 'wps'、'QQ'）。判断软件是否在运行/卡死时用。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "进程名关键词过滤，留空列出全部"}},
            "required": []}}},
    {"type": "function", "function": {"name": "cu_list_windows",
        "description": "列出全部可见窗口（标题/进程/pid/位置）。操作前先盘点桌面上开了哪些窗口、有没有遮挡。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "cu_foreground",
        "description": "把指定窗口置为前台（按标题关键词，自动还原最小化）。操作某应用前先激活它。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "窗口标题关键词，如 '记事本'、'WPS'"}},
            "required": ["title_keyword"]}}},
    {"type": "function", "function": {"name": "cu_minimize_window",
        "description": "最小化指定窗口（按标题关键词），用于清理遮挡目标的操作面。目标被遮挡/找不到时，先最小化挡路窗口再操作。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "要最小化的窗口标题关键词，如 'Edge'、'Trae'"}},
            "required": ["title_keyword"]}}},
    {"type": "function", "function": {"name": "cu_restore_window",
        "description": "还原最小化窗口到前台（按标题关键词）。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "窗口标题关键词"}},
            "required": ["title_keyword"]}}},
    {"type": "function", "function": {"name": "cu_show_desktop",
        "description": "一键显示桌面（Win+D）：最小化全部窗口露出桌面。目标被大量窗口遮挡找不到时优先调用清理全部遮挡。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "cu_check",
        "description": "断言式核验操作结果（对齐可提 mc_check）：check_type 可选 window=窗口是否存在(target=标题关键词)；file=文件是否存在(target=路径)；dir=目录是否存在；element=UI控件是否存在(target='窗口标题>>控件名')。expect: true=应成立(默认)/false=应不成立。执行操作后用它验证，不成立就修正重试。",
        "parameters": {"type": "object", "properties": {
            "check_type": {"type": "string", "enum": ["window", "file", "dir", "element"],
                            "description": "断言类型"},
            "target": {"type": "string", "description": "断言目标（格式见工具描述）"},
            "expect": {"type": "string", "default": "true", "description": "true=应成立 / false=应不成立"}},
            "required": ["check_type", "target"]}}},
]


def tools_schema_for(names: list) -> list:
    return [s for s in COMPUTER_USE_TOOLS_SCHEMA if s["function"]["name"] in names]
