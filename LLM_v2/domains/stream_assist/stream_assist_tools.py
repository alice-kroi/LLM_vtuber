# ============================================================
# LLM_v2/domains/stream_assist/stream_assist_tools.py — 工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

STREAM_ASSIST_TOOL_NAMES = [
    "sa_mark_highlight",
    "sa_list_highlights",
    "sa_queue_add",
    "sa_queue_list",
    "sa_queue_pop",
    "sa_checkin",
]

STREAM_ASSIST_TOOLS_SCHEMA: list = [
    tool_schema(
        "sa_mark_highlight",
        "[直播] 标记一个高光时刻（写本地 JSONL，供后续剪辑/复盘）。"
        "观众说“刚才很好笑/标记一下”时调用。",
        {
            "note": {"type": "string", "description": "高光说明，如“刚才那个梗”"},
            "source": {"type": "string", "description": "来源，默认 manual"},
        },
        required=["note"],
    ),
    tool_schema(
        "sa_list_highlights",
        "[直播] 列出最近标记的高光时刻。",
        {"limit": {"type": "integer", "description": "最多返回几条，默认 10",
                   "default": 10}},
    ),
    tool_schema(
        "sa_queue_add",
        "[直播] 点歌：把一首歌加入点歌队列。",
        {
            "song": {"type": "string", "description": "歌名"},
            "user": {"type": "string", "description": "点歌人，默认空"},
        },
        required=["song"],
    ),
    tool_schema(
        "sa_queue_list",
        "[直播] 查看当前点歌队列。",
        {"limit": {"type": "integer", "description": "最多返回几条，默认 20",
                   "default": 20}},
    ),
    tool_schema(
        "sa_queue_pop",
        "[直播] 取出队首的歌（唱完一首后调用）。",
    ),
    tool_schema(
        "sa_checkin",
        "[直播] 观众签到：按自然日累计连续天数。",
        {"user": {"type": "string", "description": "签到人"}},
        required=["user"],
    ),
]

__all__ = ["STREAM_ASSIST_TOOL_NAMES", "STREAM_ASSIST_TOOLS_SCHEMA"]
