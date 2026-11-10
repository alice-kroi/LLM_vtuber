# ============================================================
# LLM_v2/domains/bili_live/bili_live_tools.py — bili_live 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

BILI_LIVE_TOOL_NAMES = [
    "bili_live_start",
    "bili_live_stop",
    "bili_live_status",
    "bili_live_observe",
]

BILI_LIVE_TOOLS_SCHEMA: list = [
    tool_schema(
        "bili_live_start",
        "启动 B 站直播间弹幕/礼物/SC 监听（blivedm，自动重连）。room_ids 缺省取配置。",
        {"room_ids": {"type": "array", "items": {"type": "integer"},
                      "description": "直播间号列表（缺省用 config.json）"}},
    ),
    tool_schema("bili_live_stop", "停止 B 站直播间监听。"),
    tool_schema(
        "bili_live_status",
        "直播间监听状态：是否运行/房间/转发目标/最近错误/缓冲大小。",
    ),
    tool_schema(
        "bili_live_observe",
        "查看最近收到的弹幕/礼物/SC 消息流（缓冲窗口）。",
        {"limit": {"type": "integer", "description": "返回最近 N 条（默认 20）", "default": 20}},
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in BILI_LIVE_TOOLS_SCHEMA if s["function"]["name"] in names]
