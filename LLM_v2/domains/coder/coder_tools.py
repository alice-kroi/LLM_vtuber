# ============================================================
# LLM_v2/domains/coder/coder_tools.py — coder 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

CODER_TOOL_NAMES = [
    "code_tree",
    "code_search",
    "code_run_tests",
    "code_git_status",
    "code_git_diff",
    "code_git_restore",
]

CODER_TOOLS_SCHEMA: list = [
    tool_schema(
        "code_tree",
        "[代码] 输出目录树（自动忽略 .git/__pycache__/node_modules 等）。"
        "写代码前先看项目轮廓用这个，比截图可靠。",
        {
            "root": {"type": "string", "description": "相对工作区的目录，默认 '.'"},
            "workspace": {"type": "string", "description": "工作区根（白名单），默认系统配置值"},
            "depth": {"type": "integer", "description": "递归深度（默认 3）", "default": 3},
            "max_entries": {"type": "integer", "description": "最多条目数（默认 200）", "default": 200},
        },
    ),
    tool_schema(
        "code_search",
        "[代码] 按内容检索源码（正则，等价 ripgrep），返回 file/line/text/context。"
        "改代码第一步：先定位再动手。",
        {
            "pattern": {"type": "string", "description": "正则表达式，如 'class .*CNN' 或 'def forward'"},
            "root": {"type": "string", "description": "相对工作区的检索起点，默认 '.'", "default": "."},
            "glob": {"type": "string", "description": "文件名过滤，如 '*.py'", "default": "*"},
            "workspace": {"type": "string", "description": "工作区根（白名单）"},
            "context_lines": {"type": "integer", "description": "命中行上下文行数（默认 2）", "default": 2},
            "max_hits": {"type": "integer", "description": "最多命中数（默认 50）", "default": 50},
            "case_sensitive": {"type": "boolean", "description": "是否区分大小写（默认 false）", "default": False},
        },
        required=["pattern"],
    ),
    tool_schema(
        "code_run_tests",
        "[代码] 运行测试并结构化解析结果（passed/failed/errors/first_failure）。"
        "这是判断'改没改对'的唯一可靠信号，完成后必须调用。",
        {
            "cmd": {"type": "string", "description": "测试命令，如 'pytest -q tests/' 或 'python -m pytest -q'"},
            "cwd": {"type": "string", "description": "执行目录（相对工作区），默认工作区根"},
            "workspace": {"type": "string", "description": "工作区根（白名单）"},
            "timeout": {"type": "number", "description": "超时秒数（默认 300）", "default": 300},
        },
        required=["cmd"],
    ),
    tool_schema(
        "code_git_status",
        "[代码] git 工作区状态（分支/已改/未跟踪）。动手前确认工作区是否干净。",
        {
            "cwd": {"type": "string", "description": "仓库目录（相对工作区），默认工作区根"},
            "workspace": {"type": "string", "description": "工作区根（白名单）"},
        },
    ),
    tool_schema(
        "code_git_diff",
        "[代码] git diff：看自己改了什么，也用于给观众总结'改了哪些文件'。",
        {
            "cwd": {"type": "string", "description": "仓库目录（相对工作区）"},
            "workspace": {"type": "string", "description": "工作区根（白名单）"},
            "stat": {"type": "boolean", "description": "只看统计（默认 false）", "default": False},
        },
    ),
    tool_schema(
        "code_git_restore",
        "[代码] 回滚文件改动（改坏了用）。paths 指定文件，或 all=true 回滚全部。",
        {
            "cwd": {"type": "string", "description": "仓库目录（相对工作区）"},
            "workspace": {"type": "string", "description": "工作区根（白名单）"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "要回滚的相对路径列表"},
            "all": {"type": "boolean", "description": "是否回滚全部改动（默认 false）", "default": False},
        },
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in CODER_TOOLS_SCHEMA if s["function"]["name"] in names]
