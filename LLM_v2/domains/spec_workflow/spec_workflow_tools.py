# ============================================================
# LLM_v2/domains/spec_workflow/spec_workflow_tools.py — 工具 Schema
# ============================================================
# 七个阶段对应 SpecWorkflow（DSH 技能包）的：
#   to-grill / to-spec / spec-do / do-review / fix-review / bugs-fix
# 每个阶段只允许做它该做的事：审讯与规格阶段不写代码，
# 评审阶段只判定，修复阶段只出计划。
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

_REQ_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "需求一句话，可验收"},
        "check": {"type": "string",
                  "description": "可选：自动验证命令（退出码 0 视为通过）"},
        "id": {"type": "string", "description": "可选：自定义需求编号，如 R1"},
    },
    "required": ["text"],
}


def _requirements_param(desc: str) -> dict:
    return {"type": "array", "items": _REQ_ITEM, "description": desc}


SPEC_TOOL_NAMES = [
    "spec_triage",
    "spec_create",
    "spec_list",
    "spec_show",
    "spec_approve",
    "spec_amend",
    "spec_requirements",
    "spec_record",
    "spec_review",
    "spec_fix_plan",
    "spec_bug_report",
]

SPEC_TOOLS_SCHEMA: list = [
    tool_schema(
        "spec_triage",
        "[规格流程·第1步 需求审讯] 判断一个请求该走直接改动还是完整规格流程，"
        "并列出必须由用户拍板的问题。不写代码、不改文件。",
        {
            "request": {"type": "string", "description": "用户原始请求"},
            "files_hint": {"type": "string",
                           "description": "可选：预估涉及的文件/模块", "default": ""},
        },
        required=["request"],
    ),
    tool_schema(
        "spec_create",
        "[规格流程·第2步 写规格] 把需求写成可执行规格：目标、非目标、"
        "需求清单（每条可验收，建议带 check 命令）、验证计划。只写 .spec-workflow 产物，不写生产代码。",
        {
            "title": {"type": "string", "description": "规格标题，用于生成 slug"},
            "goal": {"type": "string", "description": "背景与目标", "default": ""},
            "requirements": _requirements_param(
                "需求清单。每条要么是字符串，要么是 {text, check, id}；"
                "能自动验证的一定给 check 命令"),
            "non_goals": {"type": "string", "description": "明确不做什么", "default": ""},
            "validation": {"type": "string",
                           "description": "验证计划概述（无 check 命令时的人工验收方式）",
                           "default": ""},
        },
        required=["title", "requirements"],
    ),
    tool_schema(
        "spec_list",
        "[规格流程] 列出已有规格及其完成度，找 id 用。",
        {"limit": {"type": "integer", "description": "条数，默认 10", "default": 10}},
    ),
    tool_schema(
        "spec_show",
        "[规格流程] 查看单个规格：目标、非目标、每条需求的状态与证据。",
        {"spec_id": {"type": "string", "description": "规格 id / slug / 标题前缀"}},
        required=["spec_id"],
    ),
    tool_schema(
        "spec_approve",
        "[规格流程·进入执行前] 审批规格，使其成为可执行规格；执行阶段只认已审批规格。",
        {
            "spec_id": {"type": "string", "description": "规格 id / slug / 标题前缀"},
            "note": {"type": "string", "description": "审批备注", "default": ""},
        },
        required=["spec_id"],
    ),
    tool_schema(
        "spec_amend",
        "[规格流程] 修订规格：追加需求或说明变更，写入 amendments/，并同步 spec.md。",
        {
            "spec_id": {"type": "string", "description": "规格 id / slug / 标题前缀"},
            "note": {"type": "string", "description": "变更说明"},
            "add_requirements": _requirements_param("新增需求（可为空）"),
        },
        required=["spec_id", "note"],
    ),
    tool_schema(
        "spec_requirements",
        "[规格流程·执行前] 取当前规格的需求账本（可直接注入编码循环的每轮提示）。",
        {"spec_id": {"type": "string", "description": "规格 id；留空取当前活跃规格",
                     "default": ""}},
    ),
    tool_schema(
        "spec_record",
        "[规格流程·执行后] 记录每条需求的证据：命令、退出码、输出摘要。没有证据不算通过。",
        {
            "spec_id": {"type": "string", "description": "规格 id"},
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "需求编号，如 R1"},
                        "status": {"type": "string",
                                   "description": "verified / failed / stale / pending",
                                   "default": "verified"},
                        "evidence": {"type": "object",
                                     "description": "证据：{command, exit_code, summary}"},
                    },
                    "required": ["id"],
                },
                "description": "逐条需求的结果",
            },
        },
        required=["spec_id", "results"],
    ),
    tool_schema(
        "spec_review",
        "[规格流程·交付评审] 按需求账本判定完成度与上线结论，写 review-report.md。"
        "只判定，不修代码、不扩大范围。",
        {"spec_id": {"type": "string", "description": "规格 id；留空取当前活跃规格",
                     "default": ""}},
    ),
    tool_schema(
        "spec_fix_plan",
        "[规格流程·评审后修复] 把未通过需求整理成修复规格（repair-spec.md），不写代码。",
        {"spec_id": {"type": "string", "description": "规格 id；留空取当前活跃规格",
                     "default": ""}},
    ),
    tool_schema(
        "spec_bug_report",
        "[规格流程·缺陷诊断] 写一份缺陷诊断报告：症状、根因、复现、修复、验证。",
        {
            "title": {"type": "string", "description": "缺陷标题"},
            "symptom": {"type": "string", "description": "症状与影响"},
            "root_cause": {"type": "string", "description": "确认的根因"},
            "repro": {"type": "string", "description": "复现命令与原始失败结果"},
            "fix": {"type": "string", "description": "修复摘要"},
            "validation": {"type": "string", "description": "验证命令与最终结果"},
        },
        required=["title", "symptom", "root_cause", "repro", "fix", "validation"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in SPEC_TOOLS_SCHEMA if s["function"]["name"] in names]
