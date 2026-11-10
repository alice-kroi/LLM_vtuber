# ============================================================
# LLM_v2/domains/desktop_pet/desktop_pet_tools.py
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

PET_TOOL_NAMES = [
    "pet_status",
    "pet_show",
    "pet_hide",
]

PET_TOOLS_SCHEMA: list = [
    tool_schema("pet_status",
        "上报桌宠当前状态（运行中/隐藏/资源占用）。",
        {}),
    tool_schema("pet_show",
        "显示桌宠窗口。",
        {}),
    tool_schema("pet_hide",
        "隐藏桌宠窗口（不退出进程）。",
        {}),
]


def tools_schema_for(names: list) -> list:
    return [s for s in PET_TOOLS_SCHEMA if s["function"]["name"] in names]