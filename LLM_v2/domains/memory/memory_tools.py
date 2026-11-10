# ============================================================
# LLM_v2/domains/memory/memory_tools.py — 记忆内核工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

MEMORY_TOOL_NAMES = [
    "memory_status", "memory_search", "memory_read",
    "memory_add", "memory_proposals", "memory_decide",
]

MEMORY_TOOLS_SCHEMA: list = [
    tool_schema(
        "memory_status",
        "查看记忆内核状态：条目/摘要/待确认建议/审计数量与数据库路径。",
        {},
    ),
    tool_schema(
        "memory_search",
        "搜索长期记忆与历史轮次摘要，返回 id、类型、文本、相关度。",
        {
            "query": {"type": "string", "description": "搜索关键词或问题"},
            "track": {
                "type": "string",
                "description": "可选：profile/global_fact/project_key/rule/turn_summary/all",
                "default": "",
            },
            "limit": {"type": "integer", "description": "返回条数", "default": 8},
        },
        required=["query"],
    ),
    tool_schema(
        "memory_read",
        "读取一条记忆或轮次摘要的完整内容与证据。",
        {"id": {"type": "string", "description": "memory_search 返回的 id"}},
        required=["id"],
    ),
    tool_schema(
        "memory_add",
        "提交一条长期记忆建议。所有稳定轨（profile/global_fact/project_key/rule）"
        "都会先进入待确认队列，用户确认后才生效。",
        {
            "track": {
                "type": "string",
                "description": "profile/global_fact/project_key/rule",
            },
            "text": {"type": "string", "description": "要记住的事实/偏好/规则"},
            "reason": {"type": "string", "description": "为什么值得长期记住"},
            "scope_key": {
                "type": "string",
                "description": "可选：项目键（project_key 轨用）",
                "default": "",
            },
        },
        required=["track", "text"],
    ),
    tool_schema(
        "memory_proposals",
        "列出待确认/已确认/已拒绝的记忆建议。",
        {
            "status": {
                "type": "string",
                "description": "pending/accepted/rejected",
                "default": "pending",
            },
            "limit": {"type": "integer", "description": "返回条数", "default": 20},
        },
    ),
    tool_schema(
        "memory_decide",
        "处理一条记忆建议：accept 采纳、reject 拒绝、edit 编辑后采纳。",
        {
            "proposal_id": {"type": "string", "description": "建议 id"},
            "action": {"type": "string", "description": "accept/reject/edit"},
            "text": {"type": "string", "description": "edit 时的新文本"},
        },
        required=["proposal_id", "action"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in MEMORY_TOOLS_SCHEMA
            if s["function"]["name"] in names]
