# ============================================================
# LLM_v2/domains/research/research.py — 联网研究能力域
# ============================================================
# 类型 Actuator：只提供搜索/读页/笔记工具，不 emit 事件、不发声。
# 子进程隔离 Playwright，避免浏览器事件循环绑定主进程。
# ============================================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    Actuator,
    CapabilityType,
    SubprocessCapabilityDomain,
)
from LLM_v2.domains.research.research_tools import (
    RESEARCH_TOOL_NAMES,
    RESEARCH_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_RESEARCH_PROMPT = """可用工具（联网研究）：
- wr_search: 搜索资料，返回标题/链接/摘要
- wr_read: 读取网页正文（研究时精读 1~2 个来源即可，不要贪多）
- wr_note_save: 保存研究笔记，供以后对话引用
- wr_notes: 检索已保存的研究笔记

执行原则：
1. 先搜索，再选 1~2 个最相关的来源精读；不要把所有结果都读一遍。
2. 资料不足时明确说"没查到"，不要编造。
3. 本域只提供资料，不负责总结成主播话术（由意志脑/主播脑完成）。
"""


class ResearchDomain(SubprocessCapabilityDomain, Actuator):
    """联网搜索与资料阅读（Actuator，默认启用）"""

    id = "research"
    label = "联网研究"
    prefix = "wr_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 60.0
    tool_timeouts = {
        "wr_search": 45.0,
        "wr_read": 45.0,
        "wr_note_save": 10.0,
        "wr_notes": 10.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "research_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(RESEARCH_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in RESEARCH_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _RESEARCH_PROMPT

    def preflight(self) -> Optional[str]:
        try:
            import playwright  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"playwright 未安装（research 域依赖浏览器搜索）: {e}"
        return None

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_research_domain() -> ResearchDomain:
    return ResearchDomain()


__all__ = ["ResearchDomain", "build_research_domain"]
