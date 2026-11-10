# ============================================================
# LLM_v2/domains/hf_hub/hf_hub_tools.py — 工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

HF_TOOL_NAMES = [
    "hf_search_models",
    "hf_model_info",
]

HF_TOOLS_SCHEMA: list = [
    tool_schema(
        "hf_search_models",
        "[HuggingFace] 按关键词检索模型库，返回模型 id、下载量、点赞数、任务标签。"
        "想知道'有没有现成的某某模型'时用这个。",
        {
            "query": {"type": "string", "description": "关键词，如 'qwen' / 'bert' / 'whisper'"},
            "limit": {"type": "integer", "description": "返回条数（1-20，默认 5）", "default": 5},
            "sort": {"type": "string", "description": "排序字段，默认 downloads（可选 likes/trendingScore）",
                     "default": "downloads"},
        },
        required=["query"],
    ),
    tool_schema(
        "hf_model_info",
        "[HuggingFace] 查询单个模型的详情：作者、任务、标签、下载量、文件列表（前 10 个）。",
        {
            "model_id": {"type": "string", "description": "模型 id，如 'Qwen/Qwen2.5-7B-Instruct'"},
        },
        required=["model_id"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in HF_TOOLS_SCHEMA if s["function"]["name"] in names]
