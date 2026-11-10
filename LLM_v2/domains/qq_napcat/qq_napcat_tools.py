# ============================================================
# LLM_v2/domains/qq_napcat/qq_napcat_tools.py — QQ(NapCat) 工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

QQ_NAPCAT_TOOL_NAMES = [
    "qq_status", "qq_send", "qq_recent",
    "qq_profile_set", "qq_avatar_set", "qq_avatar_from_web",
    "qq_profile_background_prepare", "qq_zone_post",
]

QQ_NAPCAT_TOOLS_SCHEMA: list = [
    tool_schema(
        "qq_status",
        "查看 QQ 连接状态、白名单、最近消息，以及最近操作记录 recent_ops"
        "（含 profile_set / avatar_set / avatar_from_web / zone_post 的次数与时间）。",
        {},
    ),
    tool_schema(
        "qq_send",
        "通过 QQ 主动发送一条消息（私聊或群聊）。",
        {
            "chat_type": {"type": "string", "description": "private 或 group"},
            "target": {"type": "string", "description": "私聊 QQ 号或群号"},
            "text": {"type": "string", "description": "要发送的文本"},
        },
        required=["chat_type", "target", "text"],
    ),
    tool_schema(
        "qq_recent",
        "查看最近收到的 QQ 消息（用于确认连接和路由）。",
        {"limit": {"type": "integer", "description": "最多返回条数", "default": 10}},
    ),
    tool_schema(
        "qq_profile_set",
        "修改当前 QQ 号的昵称/个性签名/性别。只改签名时不传 nickname，不会动昵称。",
        {
            "nickname": {"type": "string", "description": "新昵称；留空则只改签名"},
            "personal_note": {"type": "string", "description": "新个性签名"},
            "sex": {"type": "integer", "description": "0 未知 / 1 男 / 2 女"},
        },
    ),
    tool_schema(
        "qq_avatar_set",
        "更换当前 QQ 号头像。file 支持本地图片路径或 http(s) 链接。",
        {"file": {"type": "string", "description": "头像图片的本地路径或链接"}},
        required=["file"],
    ),
    tool_schema(
        "qq_avatar_from_web",
        "从网上搜索图片并直接设为 QQ 头像。适合“自己从网上找张图当头像”。",
        {
            "query": {"type": "string", "description": "搜索关键词，如 维尔薇 崩坏3 立绘"},
            "index": {"type": "integer", "description": "选第几个结果，默认 0", "default": 0},
        },
        required=["query"],
    ),
    tool_schema(
        "qq_profile_background_prepare",
        "下载一张适合做 QQ 资料卡背景/空间封面的图片，返回本地路径与手动设置步骤。"
        "注意：NapCat 没有修改资料卡背景的接口，无法代替用户自动设置。",
        {
            "query": {"type": "string", "description": "图片主题，如 维尔薇 崩坏3"},
        },
        required=["query"],
    ),
    tool_schema(
        "qq_zone_post",
        "在 QQ 空间发一条说说。固定为「仅自己可见」，不会公开发布。",
        {
            "content": {"type": "string", "description": "说说正文"},
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选图片路径/链接/base64",
            },
        },
        required=["content"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in QQ_NAPCAT_TOOLS_SCHEMA if s["function"]["name"] in names]
