# ============================================================
# LLM_v2/domains/webui/webui.py — WebUI Capability（H-2 Transceiver）
# ============================================================
# 声明-实现分离：本类是声明（USER_MESSAGE 的产出者），
# 实际 HTTP 端点在 LLM_v2/io/webui.py 的 _route_user_input 实现。
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List

from LLM_v2.core.capability_domain import (
    CapabilityDomain,
    CapabilityType,
    Transceiver,
    tool_schema,
)

logger = logging.getLogger(__name__)


_WEBUI_TOOL_NAMES = [
    "webui_status",
    "webui_input_history",
]

_WEBUI_TOOLS_SCHEMA: list = [
    tool_schema(
        "webui_status",
        "WebUI 连接状态：当前 WS 推送客户端数、WS 输入客户端数、最近输入消息数。",
    ),
    tool_schema(
        "webui_input_history",
        "查询最近 N 条 WebUI 输入记录（默认 20）。",
        {"limit": {"type": "integer", "default": 20}},
    ),
]


class WebUICapability(Transceiver):
    """WebUI 控制台能力域（H-2 Transceiver 类型）

    关键：WebUICapability **本身不直接处理 HTTP**。
    - HTTP 端点在 LLM_v2/io/webui.py 中
    - 端点通过 _route_user_input() emit USER_MESSAGE 事件
    - 本类只是声明"USER_MESSAGE 由 webui 能力产出"（用于 DomainAssembly 事件路由）
    """

    id = "webui"
    label = "WebUI 控制台"
    prefix = "webui_"
    default_enabled = True
    type = CapabilityType.TRANSCEIVER

    def inbound_event_types(self):
        """声明本能力 emit 的事件"""
        # WebUI 通过 REST /api/input 或 WS /ws/input 接收用户输入，
        # 经 _route_user_input emit USER_MESSAGE 事件
        return ["user_message"]

    def outbound_event_types(self):
        return []

    def allowed_names(self) -> List[str]:
        return list(_WEBUI_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in _WEBUI_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return """可用工具（H-2 WebUI 能力）：
- webui_status: 查询 WebUI 连接状态（推送/输入客户端数）
- webui_input_history: 查询最近 WebUI 输入消息

输入通道：
- POST /api/input {text, user, metadata}（REST）
- WS /ws/input（双向 WebSocket）

执行原则：
1. WebUI 收到的输入会自动 emit USER_MESSAGE(source="webui") 事件。
2. StreamerBrain 监听 USER_MESSAGE 时不关心来源，由 source 决定语调。
3. 无需主动 pushWebUI — 由 sys/ws/events 推送 STATE_UPDATED / TTS_SPEAK 等。
"""

    async def webui_status(self) -> Dict[str, Any]:
        """H-2 工具：查询 WebUI 连接状态"""
        from LLM_v2.io import webui as webui_module
        return {
            "success": True,
            "ws_push_clients": len(getattr(webui_module, "_ws_clients", set())),
            "ws_input_clients": len(getattr(webui_module, "_input_clients", set())),
            "message_history_size": len(getattr(webui_module, "_message_history", [])),
        }

    async def webui_input_history(self, limit: int = 20) -> Dict[str, Any]:
        """H-2 工具：查询最近 WebUI 输入消息"""
        from LLM_v2.io import webui as webui_module
        from LLM_v2.events import EVENTS
        all_events = list(getattr(webui_module, "_event_log", []))
        user_inputs = [
            e for e in all_events
            if e.get("event_type") == EVENTS.USER_MESSAGE
            and e.get("payload", {}).get("source") == "webui"
        ]
        return {
            "success": True,
            "count": min(limit, len(user_inputs)),
            "messages": user_inputs[-limit:],
        }


def build_webui_domain() -> WebUICapability:
    return WebUICapability()


__all__ = ['WebUICapability', 'build_webui_domain']