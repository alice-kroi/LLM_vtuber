# ============================================================
# LLM_v2/domains/desktop_pet/desktop_pet.py — desktop_pet 域类
# ============================================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Actuator,
    CapabilityType,
)
from LLM_v2.domains.desktop_pet.desktop_pet_tools import (
    PET_TOOL_NAMES, PET_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_PET_PROMPT = """可用工具：
- pet_status: 上报桌宠状态
- pet_show: 显示桌宠
- pet_hide: 隐藏桌宠

执行原则：
1. 桌宠是 UI 装饰品，不影响直播功能。
2. 默认隐藏，需要时显式调用 pet_show。
"""


class DesktopPetDomain(SubprocessCapabilityDomain, Actuator):
    """桌宠域，H-2 Actuator 类型（仅显示/隐藏桌宠，不接收输入）"""

    id = "desktop_pet"
    label = "桌宠（Bongo Cat）"
    prefix = "pet_"
    default_enabled = False     # 默认关闭（依赖桌宠进程）
    type = CapabilityType.ACTUATOR

    call_timeout = 30.0

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "desktop_pet_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(PET_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in PET_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _PET_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：桌宠可执行文件（可选，非阻塞）"""
        return None  # 桌宠未实现完整引擎时不阻塞

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_desktop_pet_domain() -> DesktopPetDomain:
    return DesktopPetDomain()


__all__ = ['DesktopPetDomain', 'build_desktop_pet_domain']