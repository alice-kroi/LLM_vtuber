# ============================================================
# LLM_v2/domains/capability_ops/capability_ops_tools.py — 工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

CAPABILITY_TOOL_NAMES = [
    "cap_list",
    "cap_verify",
    "cap_admit",
    "cap_suspend",
    "cap_resume",
    "cap_call",
    "cap_fix",
]

CAPABILITY_TOOLS_SCHEMA: list = [
    tool_schema(
        "cap_list",
        "[能力] 列出 AI 自己生成/登记过的能力：风险等级、状态、成功率、熔断阈值。"
        "想知道'我已经会什么、哪个被停用了'时用这个。",
    ),
    tool_schema(
        "cap_verify",
        "[能力] 只验证不挂载：对 LLM_v2/domains/<id>/ 依次做静态扫描（G1）、"
        "子进程导入（G2）、工具冒烟调用（G3），返回风险等级 R0-R3 与失败原因。"
        "G3 只跑 manifest.smoke 声明的工具，或 R0 且必填参数有默认值的工具。",
        {
            "path": {"type": "string",
                     "description": "域目录，如 'LLM_v2/domains/my_tool'"},
        },
        required=["path"],
    ),
    tool_schema(
        "cap_admit",
        "[能力] 验证并自动挂载一个自生成能力域。R0（只读本地）/R1（写本地）/R2（联网）自动准入；"
        "只有 R3（凭据/高副作用）需要人工确认。挂载走现有 rediscover，无需重启。",
        {
            "path": {"type": "string", "description": "域目录，如 'LLM_v2/domains/my_tool'"},
            "force": {"type": "boolean",
                      "description": "是否放宽风险上限（仍需通过 G1/G2/G3），默认 false",
                      "default": False},
            "source": {"type": "string", "description": "来源标记，默认 'explicit'",
                       "default": "explicit"},
        },
        required=["path"],
    ),
    tool_schema(
        "cap_suspend",
        "[能力] 手动熔断并回滚：停用该域；新建能力移入隔离区，已有能力恢复准入前快照。"
        "运行期连续失败/低成功率/超预算也会自动触发。",
        {
            "id": {"type": "string", "description": "能力 id（域目录名）"},
            "reason": {"type": "string", "description": "熔断原因", "default": "manual"},
        },
        required=["id"],
    ),
    tool_schema(
        "cap_resume",
        "[能力] 恢复一个被熔断/隔离的能力：目录移回、清零连续失败计数、重新挂载。",
        {
            "id": {"type": "string", "description": "能力 id（域目录名）"},
        },
        required=["id"],
    ),
    tool_schema(
        "cap_call",
        "[能力] 经管理域调用自生成能力的工具（会先检查熔断状态）。"
        "调用自己新造的能力时优先用它，便于统计与熔断。",
        {
            "tool": {"type": "string", "description": "要调用的工具名"},
            "arguments": {"type": "object", "description": "工具参数对象，默认空"},
        },
        required=["tool"],
    ),
    tool_schema(
        "cap_fix",
        "[能力] 人工把能力标记为 fixed（不再自动降级），或取消固定回到 derived。"
        "能力阶段：derived（单次成功生成）→ captured（连续成功自动固化）→ fixed（人工确认）。",
        {
            "id": {"type": "string", "description": "能力 id（域目录名）"},
            "fixed": {"type": "boolean", "description": "true=固定，false=取消固定",
                      "default": True},
        },
        required=["id"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in CAPABILITY_TOOLS_SCHEMA if s["function"]["name"] in names]
