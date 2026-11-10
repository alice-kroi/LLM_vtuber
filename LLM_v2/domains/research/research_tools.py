# ============================================================
# LLM_v2/domains/research/research_tools.py — research 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

RESEARCH_TOOL_NAMES = ["wr_search", "wr_read", "wr_note_save", "wr_notes"]

RESEARCH_TOOLS_SCHEMA: list = [
    tool_schema(
        "wr_search",
        "联网搜索资料。返回标题、链接和摘要；适合查询最新信息、新闻、技术文档。",
        {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最多返回结果数", "default": 5},
        },
        required=["query"],
    ),
    tool_schema(
        "wr_read",
        "读取网页正文，供研究时精读资料。自动提取 article/main 正文。",
        {
            "url": {"type": "string", "description": "目标网页 URL（http/https）"},
            "max_chars": {"type": "integer", "description": "正文最大字符数", "default": 3000},
        },
        required=["url"],
    ),
    tool_schema(
        "wr_note_save",
        "保存一条研究笔记（同一主题覆盖旧版本），供以后对话和自主决策引用。",
        {
            "topic": {"type": "string", "description": "研究主题"},
            "summary": {"type": "string", "description": "整理后的要点"},
            "sources": {"type": "array", "items": {"type": "string"},
                        "description": "来源 URL 列表"},
            "tags": {"type": "string", "description": "可选标签，逗号分隔", "default": ""},
        },
        required=["topic", "summary"],
    ),
    tool_schema(
        "wr_notes",
        "检索已保存的研究笔记。",
        {
            "query": {"type": "string", "description": "关键词；留空取最近笔记", "default": ""},
            "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
        },
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in RESEARCH_TOOLS_SCHEMA if s["function"]["name"] in names]
