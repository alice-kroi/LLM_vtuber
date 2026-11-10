# ============================================================
# LLM_v2/domains/desktop_agent/desktop_agent.py — desktop_agent 域类
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
from LLM_v2.domains.desktop_agent.desktop_agent_tools import (
    DSK_TOOL_NAMES, DSK_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_DSK_PROMPT = """可用工具（Win32 + UIA 桌面感知）：
- dsk_windows: 列出当前所有可见窗口（标题/位置/尺寸）
- dsk_activate: 把窗口置为前台
- dsk_list_elements: UIA 读取窗口内控件真实坐标（标准控件最准）
- dsk_click_xy: 屏幕坐标点击
- dsk_stop: 释放资源

执行原则：
1. 操作标准 Windows 应用优先用 dsk_list_elements 拿真实坐标。
2. webview / 游戏界面用 desktop 域的 vision_click。
4. dsk_* 主要服务 UIA 树查找，AI 操作主路径走 desktop 域。
"""


class DesktopAgentDomain(SubprocessCapabilityDomain, Actuator):
    """桌面代理域（V2 子进程域，H-2 Actuator 类型）"""

    id = "desktop_agent"
    label = "桌面代理（Win32 + UIA）"
    prefix = "dsk_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 60.0
    tool_timeouts = {
        "dsk_windows": 10.0,
        "dsk_activate": 15.0,
        "dsk_list_elements": 30.0,
        "dsk_click_xy": 10.0,
        "dsk_status": 10.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "desktop_agent_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(DSK_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in DSK_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _DSK_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：pyautogui + uiautomation"""
        try:
            import pyautogui  # noqa
            import uiautomation  # noqa
            return None
        except ImportError as e:
            return f"desktop_agent 域依赖缺失: {e}"

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_desktop_agent_domain() -> DesktopAgentDomain:
    return DesktopAgentDomain()


__all__ = ['DesktopAgentDomain', 'build_desktop_agent_domain']