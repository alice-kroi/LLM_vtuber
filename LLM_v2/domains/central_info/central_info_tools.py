# ============================================================
# LLM_v2/domains/central_info/central_info_tools.py — 中央信息工具 Schema
# ============================================================
# 固定层（问题/需求）只有读工具 ci_brief，不存在写工具：
# 操作脑在接口层面就无法修改问题与需求，只能推进任务（ci_task_update）。
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

CENTRAL_INFO_TOOL_NAMES = [
    "ci_overview",
    "ci_brief",
    "ci_tasks",
    "ci_task_update",
    "ci_domain",
    "ci_chat",
]

CENTRAL_INFO_TOOLS_SCHEMA: list = [
    tool_schema(
        "ci_overview",
        "读取中央信息（固定顺序）：问题与需求 → 任务进度 → 各域卡片/索引 → 近期聊天。"
        "这是了解全局状态的首选入口。",
        {
            "chat_limit": {"type": "integer", "description": "返回最近多少条聊天", "default": 6},
            "domain_limit": {"type": "integer", "description": "最多列出多少个域", "default": 32},
        },
    ),
    tool_schema(
        "ci_brief",
        "[固定层·只读] 当前问题与需求：目标、验收条目及状态、非目标、验证计划。"
        "来自已审批规格；此信息不可修改，改需求要走规格修订。",
        {"spec_id": {"type": "string", "description": "可选：指定规格；留空取当前活跃规格",
                     "default": ""}},
    ),
    tool_schema(
        "ci_tasks",
        "[可变层] 任务进度表（GFM 表格）：任务、相位、百分比、阶段、说明、revision。",
        {
            "limit": {"type": "integer", "description": "条数，默认 20", "default": 20},
            "include_terminal": {"type": "boolean",
                                 "description": "是否包含已完成/已失败任务", "default": False},
        },
    ),
    tool_schema(
        "ci_task_update",
        "[可变层·写] 推进任务：更新相位/进度/阶段/说明。必须带 revision（乐观锁），"
        "revision 过期会被拒绝；done/failed 为终态不可回退。"
        "task_id 留空表示新建任务（用 title 命名）。",
        {
            "task_id": {"type": "string", "description": "任务 id；留空则新建", "default": ""},
            "title": {"type": "string", "description": "新建任务时的标题", "default": ""},
            "revision": {"type": "integer", "description": "任务当前 revision（新建时忽略）",
                         "default": 0},
            "phase": {"type": "string",
                      "description": "pending / running / blocked / done / failed",
                      "default": ""},
            "percent": {"type": "integer", "description": "0-100", "default": -1},
            "stage": {"type": "string", "description": "当前阶段，如 coding:verify",
                      "default": ""},
            "detail": {"type": "string", "description": "一句话说明当前情况",
                       "default": ""},
            "actor": {"type": "string", "description": "谁在写：operator/will/streamer 等",
                      "default": "operator"},
        },
    ),
    tool_schema(
        "ci_domain",
        "读取某个能力域的中央卡片：摘要、当前进展、以及该域详情/操作的渠道（工具名）。",
        {"domain_id": {"type": "string", "description": "能力域 id，如 cultist"}},
        required=["domain_id"],
    ),
    tool_schema(
        "ci_chat",
        "读取最近的跨来源对话记录（B站/WebUI/CLI 等）。",
        {"limit": {"type": "integer", "description": "返回最近多少条", "default": 10}},
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in CENTRAL_INFO_TOOLS_SCHEMA if s["function"]["name"] in names]
