# ============================================================
# LLM_v2/domains/memory/memory.py — 记忆内核能力域
# ============================================================
from __future__ import annotations

from typing import Any, Dict, List

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.memory.memory_tools import (
    MEMORY_TOOL_NAMES,
    MEMORY_TOOLS_SCHEMA,
)
from persona.memory.service import get_memory_service


class MemoryDomain(Actuator):
    """把 Memory Kernel 暴露成能力域工具。"""

    id = "memory"
    label = "记忆内核"
    prefix = "memory_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    tool_timeouts = {
        "memory_status": 10.0,
        "memory_search": 20.0,
        "memory_read": 10.0,
        "memory_add": 10.0,
        "memory_proposals": 10.0,
        "memory_decide": 10.0,
    }

    def allowed_names(self) -> List[str]:
        return list(MEMORY_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in MEMORY_TOOLS_SCHEMA
                if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return (
            "【记忆内核】memory_search 搜索历史摘要/长期记忆；memory_read 读全文；"
            "memory_add 提交长期记忆建议（先进待确认队列）；"
            "memory_proposals 查看建议；memory_decide 采纳/拒绝。"
            "稳定记忆必须用户确认后才生效。"
        )

    def central_card(self) -> Dict[str, Any]:
        return {
            "summary": "记忆内核：轮次摘要 + 长期记忆 + 待确认建议",
            "channels": [{
                "name": "记忆查询",
                "tools": ["memory_search", "memory_status"],
                "description": "检索历史摘要与长期记忆",
            }],
        }

    async def start(self) -> None:
        await get_memory_service().start()

    async def memory_status(self) -> Dict[str, Any]:
        return await get_memory_service().status()

    async def memory_search(self, query: str, track: str = "",
                            limit: int = 8) -> Dict[str, Any]:
        return await get_memory_service().search(
            query, track=track, limit=limit)

    async def memory_read(self, id: str) -> Dict[str, Any]:
        return await get_memory_service().read(id)

    async def memory_add(self, track: str, text: str, reason: str = "",
                         scope_key: str = "") -> Dict[str, Any]:
        return await get_memory_service().add(
            track, text, reason=reason, scope_key=scope_key)

    async def memory_proposals(self, status: str = "pending",
                               limit: int = 20) -> Dict[str, Any]:
        return await get_memory_service().proposals(
            status=status, limit=limit)

    async def memory_decide(self, proposal_id: str, action: str,
                            text: str = "") -> Dict[str, Any]:
        return await get_memory_service().decide(
            proposal_id, action, text=text or None)


def build_memory_domain() -> MemoryDomain:
    return MemoryDomain()


__all__ = ["MemoryDomain", "build_memory_domain"]
