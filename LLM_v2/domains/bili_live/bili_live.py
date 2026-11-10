# ============================================================
# LLM_v2/domains/bili_live/bili_live.py — 直播间监听域类
# ============================================================
# 原 LLM_v2/domains/bili/bili.py（域 id 由 bili 更名为 bili_live，
# 与新增的 bili_video 视频域拆分开）。
# ============================================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Transceiver,
    CapabilityType,
)
from LLM_v2.domains.bili_live.bili_live_tools import (
    BILI_LIVE_TOOL_NAMES,
    BILI_LIVE_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_BILI_LIVE_PROMPT = """可用工具（B 站直播间监听）：
- bili_live_start: 启动 B 站直播间弹幕/礼物/SC 监听（默认 room_ids 来自配置）
- bili_live_stop: 停止监听
- bili_live_status: 上报当前监听状态
- bili_live_observe: 查看最近弹幕/礼物/SC 缓冲

执行原则：
1. 默认依赖配置的 ROOM_IDS，无需手动指定。
2. bili_live_start 自动重连，启动失败请看 status 里的最近错误。
3. bili_live_observe 只读，绝不修改弹幕；如需回复请用 bilibili_reply 事件。
"""


class BiliLiveDomain(SubprocessCapabilityDomain, Transceiver):
    """B 站直播间监听能力域（V2 子进程域，Transceiver 类型）

    H-2 说明：
        - 类型 Transceiver（接收弹幕消息 + 提供监听控制工具）
        - inbound_event_types 由 BiliListener 实际触发（USER_MESSAGE / GIFT_RECEIVED）
          本类声明用于 DomainAssembly 事件路由表登记
    """

    id = "bili_live"
    label = "B 站直播间监听"
    prefix = "bili_live_"
    default_enabled = False     # 默认关闭（依赖 SESSDATA 环境变量 + bilibili 直播间）
    type = CapabilityType.TRANSCEIVER

    def inbound_event_types(self):
        """声明本能力 emit 的事件（由监听子进程实际触发）"""
        # 注：USER_MESSAGE 由 WebUI / pet / cli 等多个能力共享 emit（同一事件可多 Receiver），
        # bili_live 是 USER_MESSAGE 的多个 Receiver 之一。GIFT_RECEIVED 暂由 bili_live 独占。
        return ["gift_received"]

    def outbound_event_types(self):
        return []

    call_timeout = 60.0
    tool_timeouts = {
        "bili_live_start": 30.0,
        "bili_live_stop": 15.0,
        "bili_live_status": 10.0,
        "bili_live_observe": 10.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "bili_live_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(BILI_LIVE_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in BILI_LIVE_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _BILI_LIVE_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：BILIBILI_SESSDATA 环境变量"""
        sessdata = os.environ.get("BILIBILI_SESSDATA")
        if not sessdata:
            return "BILIBILI_SESSDATA 环境变量未设置（bili_live 域需登录态）"
        return None

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_bili_live_domain() -> BiliLiveDomain:
    return BiliLiveDomain()


__all__ = ['BiliLiveDomain', 'build_bili_live_domain']
