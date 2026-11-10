# ============================================================
# LLM_v2/domains/ui_perception/ui_perception_tools.py
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

UIP_TOOL_NAMES = [
    "uip_list_windows",
    "uip_read_tree",
    "uip_locate",
    "uip_ocr",
    "uip_ground",
    "uip_capture",
    "uip_status",
    "uip_stop",
]

UIP_TOOLS_SCHEMA: list = [
    tool_schema("uip_list_windows",
        "列出当前所有可见窗口（标题/位置/尺寸）。",
        {"limit": {"type": "integer", "default": 30}}),
    tool_schema("uip_read_tree",
        "读取指定窗口的 UI Automation 控件树（深度可控）。",
        {"window_title": {"type": "string"},
         "depth": {"type": "integer", "default": 5}},
        required=["window_title"]),
    tool_schema("uip_locate",
        "按控件名/类型在窗口内定位，返回真实像素坐标。",
        {"window_title": {"type": "string"},
         "name": {"type": "string"},
         "control_type": {"type": "string"}},
        required=["window_title", "name"]),
    tool_schema("uip_ocr",
        "对指定窗口截图并 OCR 识别文字（含坐标）。",
        {"window_title": {"type": "string"}},
        required=["window_title"]),
    tool_schema("uip_ground",
        "视觉 grounding：描述目标 → 模型返回坐标（豆包 GUI 模型）。",
        {"window_title": {"type": "string"},
         "description": {"type": "string"}},
        required=["window_title", "description"]),
    tool_schema("uip_capture",
        "截取指定窗口的截图（base64）。",
        {"window_title": {"type": "string"}},
        required=["window_title"]),
    tool_schema("uip_status",
        "上报底层依赖（uiautomation / 豆包 API）状态。",
        {}),
    tool_schema("uip_stop",
        "释放临时缓存。",
        {}),
]


def tools_schema_for(names: list) -> list:
    return [s for s in UIP_TOOLS_SCHEMA if s["function"]["name"] in names]