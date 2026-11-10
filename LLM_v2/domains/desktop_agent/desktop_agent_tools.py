# ============================================================
# LLM_v2/domains/desktop_agent/desktop_agent_tools.py
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

DSK_TOOL_NAMES = [
    "dsk_windows",
    "dsk_activate",
    "dsk_list_elements",
    "dsk_click_xy",
    "dsk_status",
    "dsk_stop",
]

DSK_TOOLS_SCHEMA: list = [
    tool_schema("dsk_windows",
        "列出当前所有可见窗口（标题/位置/尺寸），用于盘点操作面。",
        {"limit": {"type": "integer", "default": 30}}),
    tool_schema("dsk_activate",
        "把窗口置为前台（按标题关键词模糊匹配）。",
        {"title_keyword": {"type": "string", "description": "窗口标题关键词"}},
        required=["title_keyword"]),
    tool_schema("dsk_list_elements",
        "用 UI Automation 读取窗口内控件的真实坐标（Win32 控件，标准应用最准）。",
        {"window_title": {"type": "string"},
         "limit": {"type": "integer", "default": 40}},
        required=["window_title"]),
    tool_schema("dsk_click_xy",
        "在屏幕绝对像素坐标点击。",
        {"x": {"type": "integer"}, "y": {"type": "integer"},
         "clicks": {"type": "integer", "default": 1},
         "button": {"type": "string", "default": "left"}},
        required=["x", "y"]),
    tool_schema("dsk_status",
        "上报底层依赖（pyautogui/uiautomation）状态。",
        {}),
    tool_schema("dsk_stop",
        "释放底层资源（关闭可能打开的辅助窗口）。",
        {}),
]


def tools_schema_for(names: list) -> list:
    return [s for s in DSK_TOOLS_SCHEMA if s["function"]["name"] in names]