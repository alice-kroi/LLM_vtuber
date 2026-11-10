# ============================================================
# LLM_v2/domains/eval_lab/eval_lab_tools.py — 评测工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

EVAL_TOOL_NAMES = ["eval_run", "eval_history", "eval_show", "eval_compare"]

EVAL_TOOLS_SCHEMA: list = [
    tool_schema(
        "eval_run",
        "[评测] 跑一个基准文件（evals/benchmarks/<name>.json），逐用例执行检查命令，"
        "产出 JSON + Markdown 报告并落库。用于回答“这次改动到底变好还是变坏”。",
        {
            "benchmark": {"type": "string",
                          "description": "基准名（不含 .json）或完整路径"},
            "case_ids": {"type": "array", "items": {"type": "string"},
                         "description": "只跑指定用例（可省略）"},
        },
        required=["benchmark"],
    ),
    tool_schema(
        "eval_history",
        "[评测] 列出历史运行（时间、用例数、通过数、成功率）。",
        {"limit": {"type": "integer", "description": "条数，默认 10", "default": 10}},
    ),
    tool_schema(
        "eval_show",
        "[评测] 查看单次运行详情：逐用例结果、耗时、失败原因。",
        {"run_id": {"type": "string", "description": "运行 id（支持前缀）"}},
        required=["run_id"],
    ),
    tool_schema(
        "eval_compare",
        "[评测] 配对 A/B：两次运行逐用例比较 胜/负/平，并给出符号检验 p 值。"
        "a=基线，b=候选。",
        {
            "run_a": {"type": "string", "description": "基线运行 id（支持前缀）"},
            "run_b": {"type": "string", "description": "候选运行 id（支持前缀）"},
        },
        required=["run_a", "run_b"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in EVAL_TOOLS_SCHEMA if s["function"]["name"] in names]
