# ============================================================
# LLM_v2/domains/launcher/launcher_tools.py — launcher 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

LAUNCHER_TOOL_NAMES = [
    "launcher_start_all",
    "launcher_mumu",
    "launcher_alas",
    "launcher_maa",
    "launcher_status",
    "launcher_stop",
]

LAUNCHER_TOOLS_SCHEMA: list = [
    tool_schema(
        "launcher_start_all",
        "一键底层启动：拉起 MuMu 指定模拟器实例 + AzurLane Auto Script(ALAS) + MAA，"
        "并在各自主界面点击启动按钮开始挂机。底层直接调 exe 与系统指挥，非视觉。",
        {
            "instances": {"type": "array", "items": {"type": "integer"},
                          "description": "要启动的模拟器实例编号（默认 [0,1]）"},
            "click_start": {"type": "boolean", "description": "是否在各自主界面点启动按钮（默认 true）"},
        },
    ),
    tool_schema(
        "launcher_mumu",
        "用 MuMuManager 底层启动指定编号的模拟器实例。",
        {"instances": {"type": "array", "items": {"type": "integer"},
                       "description": "实例编号数组，如 [0,1] 或 [1]"}},
    ),
    tool_schema(
        "launcher_alas",
        "底层启动 AzurLane Auto Script(alas.exe)。",
    ),
    tool_schema(
        "launcher_maa",
        "底层启动 MAA(maa.exe)。",
    ),
    tool_schema(
        "launcher_status",
        "上报当前 MuMu/ALAS/MAA 相关窗口在不在，用于确认是否已进入应用。",
    ),
    tool_schema(
        "launcher_stop",
        "停止 launcher 域本轮编排（保留已启动的应用；不关闭模拟器/游戏）。",
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in LAUNCHER_TOOLS_SCHEMA if s["function"]["name"] in names]