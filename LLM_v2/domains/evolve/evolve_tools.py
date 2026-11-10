# ============================================================
# LLM_v2/domains/evolve/evolve_tools.py — 自进化工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

EVOLVE_TOOL_NAMES = ["evolve_add", "evolve_remove", "evolve_list"]

EVOLVE_TOOLS_SCHEMA: list = [
    tool_schema(
        "evolve_add",
        "给自己新增一个可持久化的工具（自进化）。会生成独立能力域、跑准入检查并热挂载；"
        "新工具从下一轮起可调用，重启后仍保留。适用于反复出现、当前没有的能力："
        "跨会话状态、实时外部数据、精确计算、用户私有信息。"
        "code 可留空，由工具按 goal 自动生成 handler.py；也可直接给完整 Python 源码。"
        "handler 必须定义 run(**kwargs) 并返回 dict（含 success）。"
        "默认会做语义去重：已有同义工具时会被拦下；确认要做不同的新工具才传 force=true。"
        "生成代码与主进程同权限，仅用于可信自用环境。",
        {
            "goal": {
                "type": "string",
                "description": "这个工具要解决什么问题、输入输出示例、需要固化的用户私有信息",
            },
            "name": {
                "type": "string",
                "description": "进化名，kebab-case，如 temperature-convert；会成为域 id evo_temperature_convert",
            },
            "tool_name": {
                "type": "string",
                "description": "工具名，必须以 evo_ 开头，如 evo_temperature_convert；留空则自动生成",
            },
            "description": {
                "type": "string",
                "description": "给模型看的工具说明：什么时候该调用它",
            },
            "parameters": {
                "type": "object",
                "description": "可选：JSON Schema properties（参数名 -> {type, description}）",
            },
            "code": {
                "type": "string",
                "description": "可选：完整 handler.py 源码（必须定义 run(**kwargs)）；留空则由工具自动生成",
            },
            "risk": {
                "type": "string",
                "description": "风险等级 R0/R1/R2/R3；默认 R2。R3 需人工确认",
                "default": "R2",
            },
            "force": {
                "type": "boolean",
                "description": "确认要做不同的新工具，忽略语义重复检查（默认 false）",
                "default": False,
            },
        },
        required=["goal", "name"],
    ),
    tool_schema(
        "evolve_remove",
        "卸载一个自进化工具：停止并移除能力域、清理准入记录；默认删除源码。",
        {
            "name": {
                "type": "string",
                "description": "进化名（kebab-case）或域 id（evo_xxx）",
            },
            "keep_source": {
                "type": "boolean",
                "description": "是否保留源码文件（默认 false）",
                "default": False,
            },
        },
        required=["name"],
    ),
    tool_schema(
        "evolve_list",
        "列出当前所有自进化工具：名称、工具名、状态、版本、描述、源码路径。",
        {},
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in EVOLVE_TOOLS_SCHEMA if s["function"]["name"] in names]
