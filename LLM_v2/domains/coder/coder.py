# ============================================================
# LLM_v2/domains/coder/coder.py — coder 能力域（代码工程操作）
# ============================================================
# Actuator 类型：只提供工具，无 inbound 事件。
# 工具实现都在 tool/code_ops.py（与 computer_use.py 并列，不混入 desktop 域）。
# 设计文档：docs/AI_CODING_CAPABILITY.md §4.1 / §7
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityDomain, CapabilityType
from LLM_v2.domains.coder.coder_tools import CODER_TOOL_NAMES, CODER_TOOLS_SCHEMA

logger = logging.getLogger(__name__)


# 工具超时（域声明，调用方不硬编码）
CODER_TOOL_TIMEOUTS = {
    "code_tree": 30.0,
    "code_search": 60.0,
    "code_run_tests": 320.0,
    "code_git_status": 20.0,
    "code_git_diff": 20.0,
    "code_git_restore": 30.0,
}


class CoderDomain(Actuator):
    """代码工程操作能力域（V2 Actuator）"""

    id = "coder"
    label = "代码工程操作"
    prefix = "code_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 320.0
    tool_timeouts = dict(CODER_TOOL_TIMEOUTS)

    def __init__(self):
        super().__init__()
        from tool import code_ops
        for name in CODER_TOOL_NAMES:
            func = getattr(code_ops, name, None)
            if func is not None:
                setattr(self, name, func)
            else:
                logger.warning(f"[CoderDomain] tool.code_ops.{name} 不存在")

    def allowed_names(self) -> List[str]:
        return list(CODER_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(CODER_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return (
            "可用工具（代码工程操作，工作区沙箱内）：\n"
            "- code_tree: 目录树（忽略 .git/__pycache__/node_modules）\n"
            "- code_search: 按内容检索（正则，等价 ripgrep）\n"
            "- code_run_tests: 运行测试并结构化解析（passed/failed/first_failure）\n"
            "- code_git_status / code_git_diff / code_git_restore: git 状态/差异/回滚\n"
            "执行原则：\n"
            "1. 所有路径必须落在工作区内，越界会被拒绝。\n"
            "2. 改代码流程：code_git_status → code_tree → code_search → 读关键文件 → 改 → code_run_tests。\n"
            "3. **未跑验证的改动不算完成**；测试失败要把 first_failure 作为下一步的依据。\n"
            "4. 危险命令（rm -rf / git push --force / format 等）会被安全策略拦截。\n"
            "5. 这些工具走异步线程池，不阻塞直播主流程。\n"
        )

    def preflight(self) -> Optional[str]:
        try:
            import git  # noqa: F401 触发 git 命令可用性检查
        except ImportError:
            pass
        try:
            from tool import code_ops  # noqa: F401
        except Exception as e:
            return f"coder 工具加载失败: {e}"
        return None


def build_coder_domain() -> CoderDomain:
    return CoderDomain()


__all__ = ['CoderDomain', 'build_coder_domain', 'CODER_TOOL_TIMEOUTS']