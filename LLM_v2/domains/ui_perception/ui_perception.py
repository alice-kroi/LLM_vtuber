# ============================================================
# LLM_v2/domains/ui_perception/ui_perception.py — UI 感知域类
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
from LLM_v2.domains.ui_perception.ui_perception_tools import (
    UIP_TOOL_NAMES, UIP_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_UIP_PROMPT = """可用工具（UIA 树 + OCR + 视觉 grounding）：
- uip_list_windows: 列可见窗口
- uip_read_tree: 读窗口内 UI Automation 控件树（结构化坐标）
- uip_locate: 按控件名 / 类型定位真实像素坐标
- uip_ocr: 截图 + OCR 识别（含坐标）
- uip_ground: 视觉 grounding（描述目标 → 坐标）
- uip_capture: 截图（base64）
- uip_status: 底层依赖状态

执行原则：
1. 已知控件名/类型 → uip_locate（最快、最准）；
2. 未知元素 → uip_ground（语义化找元素）；
3. 需要看整个画面文字 → uip_ocr；
4. 复杂界面分多层组合：uip_list_windows → uip_read_tree → uip_locate。
"""


class UIPerceptionDomain(SubprocessCapabilityDomain, Actuator):
    """UI 感知域（V2 子进程域，H-2 Actuator 类型）"""

    id = "ui_perception"
    label = "UI 感知（UIA + OCR + 视觉）"
    prefix = "uip_"
    default_enabled = False     # 默认关闭（依赖 uiautomation + 豆包 API）
    type = CapabilityType.ACTUATOR

    call_timeout = 60.0
    tool_timeouts = {
        "uip_list_windows": 10.0,
        "uip_read_tree": 30.0,
        "uip_locate": 30.0,
        "uip_ocr": 60.0,
        "uip_ground": 60.0,
        "uip_capture": 30.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "ui_perception_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(UIP_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in UIP_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _UIP_PROMPT

    def preflight(self) -> Optional[str]:
        try:
            import uiautomation  # noqa
            return None
        except ImportError as e:
            return f"ui_perception 域依赖缺失: {e}"

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_ui_perception_domain() -> UIPerceptionDomain:
    return UIPerceptionDomain()


__all__ = ['UIPerceptionDomain', 'build_ui_perception_domain']