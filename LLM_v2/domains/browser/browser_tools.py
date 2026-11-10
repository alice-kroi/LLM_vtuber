# ============================================================
# LLM_v2/domains/browser/browser_tools.py — browser 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

BROWSER_TOOL_NAMES = [
    "br_navigate", "br_snapshot", "br_get_content", "br_click",
    "br_type", "br_press_key", "br_scroll", "br_tabs",
    "br_screenshot", "br_wait", "br_stop",
]

BROWSER_TOOLS_SCHEMA: list = [
    tool_schema("br_navigate",
        "受控 Edge 导航到指定 URL（自动等待加载，返回页面快照）。",
        {"url": {"type": "string", "description": "目标 URL，需以 http:// 或 https:// 开头"}},
        required=["url"]),
    tool_schema("br_snapshot",
        "获取当前页面可交互元素列表（带 @eN 引用）+ URL/Title。",
        {}),
    tool_schema("br_get_content",
        "读取当前页面正文文本（前 3000 字）。",
        {}),
    tool_schema("br_click",
        "点击页面元素。ref 来自 br_snapshot 的 @eN 引用。",
        {"ref": {"type": "string", "description": "元素引用，如 @e3"}},
        required=["ref"]),
    tool_schema("br_type",
        "在页面输入框输入文本。",
        {"ref": {"type": "string", "description": "输入框引用"},
         "text": {"type": "string", "description": "要输入的文本"},
         "clear_first": {"type": "boolean", "default": True},
         "auto_submit": {"type": "boolean", "default": False}},
        required=["ref", "text"]),
    tool_schema("br_press_key",
        "在页面按键：Enter/Tab/Esc/ArrowUp/ArrowDown/Home/End/PageUp/PageDown/F5/Ctrl+R 等。",
        {"key": {"type": "string", "description": "按键名"}},
        required=["key"]),
    tool_schema("br_scroll",
        "滚动页面（up/down/left/right，默认下滚 500px）。",
        {"direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
         "amount": {"type": "integer", "default": 500}},
        required=["direction"]),
    tool_schema("br_tabs",
        "标签页管理：list/new/switch/close。",
        {"action": {"type": "string", "enum": ["list", "new", "switch", "close"]},
         "ref": {"type": "string", "description": "tab 引用"},
         "url": {"type": "string", "description": "new 时初始 URL"}},
        required=["action"]),
    tool_schema("br_screenshot",
        "截取当前页面（base64 + 保存 PNG）。",
        {}),
    tool_schema("br_wait",
        "等待若干秒，默认 1 秒。",
        {"seconds": {"type": "number", "default": 1.0}}),
    tool_schema("br_stop",
        "关闭受控 Edge 浏览器实例。",
        {}),
]


def tools_schema_for(names: list) -> list:
    return [s for s in BROWSER_TOOLS_SCHEMA if s["function"]["name"] in names]