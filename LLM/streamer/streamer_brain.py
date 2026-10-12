#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播脑门面层（Streamer Front）：对外收发 + L0/L1 简单思考 + 编排层桥接。

职责（阶段2，LangGraph 保留演进）：
- ingress：接收标准化消息 → 会话关联 → L0 规则 / L1 小模型简单思考
  → 能答的直接答（egress 路由回渠道）；需要大模型/信息/操作 → 编排层（LangGraph）
- orchestrate：把请求交给编排层（由 main.py 注入 handle_messages / LangGraphManager）
- polish：persona 情感化润色（阶段2 轻量直通，阶段3 强化）
- egress：按渠道路由回复（bili/webui/cli/api）

复用：message.StreamerMessage、session.SessionManager、small_brain.SmallBrain、
channel.CHANNELS。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from LLM.streamer.channel import CHANNELS
from LLM.streamer.message import StreamerMessage
from LLM.streamer.session import SessionManager
from LLM.streamer.small_brain import SmallBrain, get_small_brain

logger = logging.getLogger(__name__)

Orchestrator = Callable[[list], Any]   # 接收 OpenAI 消息列表，交给 LangGraph 编排


class StreamerFront:
    """主播脑门面：收发 + 简单思考 + 编排桥接。"""

    def __init__(self, small_brain: Optional[SmallBrain] = None):
        self.small_brain = small_brain or get_small_brain()
        self._orch: Optional[Orchestrator] = None
        self._default_channel = "cli"
        self.persona: Dict[str, Any] = self._load_persona()

    @staticmethod
    def _load_persona() -> Dict[str, Any]:
        """从 skill_worlds.json 的 streamer.persona 读 persona（口吻/尾缀/口头禅）。"""
        try:
            import json as _json
            import os as _os
            path = _os.path.join(_os.path.dirname(_os.path.dirname(
                _os.path.abspath(__file__))), "skill_worlds.json")
            with open(path, encoding="utf-8") as f:
                cfg = _json.load(f)
            return (cfg.get("streamer") or {}).get("persona") or {}
        except Exception:
            return {}

    def bind_orchestrator(self, orch: Orchestrator) -> None:
        """绑定编排层（main.py 注入 handle_messages）。"""
        self._orch = orch
        logger.info("[StreamerFront] 编排层已绑定")

    # ---------- 入口 ----------
    async def ingress(self, msg: StreamerMessage, sessions: SessionManager) -> Dict[str, Any]:
        """统一入口：会话 → L0/L1 小模型 → 能答直接答 / 需要则进编排层。"""
        sess = sessions.get(msg.channel, msg.user_id)
        sessions.append(msg.channel, msg.user_id, msg.to_openai_dict())

        # L0 规则模板 + L1 小模型简单思考（本地，几乎零 API 成本）
        small = await self.small_brain.simple_think(msg.content, sess)
        if small.get("reply"):
            return await self.egress(small["reply"], sess, msg.channel)
        if not small.get("need_big"):
            # 小模型既没给出回复、也没判定需要大模型（罕见）→ 兜底进编排层，不漏需求
            logger.warning(f"[StreamerFront] small_brain 空结果，兜底进编排层: {msg.content[:30]}")
        return await self.orchestrate(msg, sess)

    # ---------- 编排层桥接 ----------
    async def orchestrate(self, msg: StreamerMessage, sess) -> Dict[str, Any]:
        """交给编排层（LangGraph）处理：信息管线 / 思维决策 / 操作路由。"""
        if self._orch is None:
            logger.error("[StreamerFront] 编排层未绑定（main 需 bind_orchestrator）")
            return {"success": False, "error": "编排层未绑定",
                    "channel": msg.channel, "user": msg.user_id}
        try:
            await self._orch([msg.to_openai_dict()])
            return {"success": True, "note": "已交编排层",
                    "channel": msg.channel, "user": msg.user_id}
        except Exception as e:
            logger.error(f"[StreamerFront] 编排层调用失败: {e}")
            return {"success": False, "error": str(e)}

    # ---------- 输出 ----------
    def polish(self, reply: str, sess=None) -> str:
        """persona 情感化润色（免费规则）：尾缀 + 口头禅，让回复更有人味。"""
        if not reply:
            return reply
        text = reply.rstrip("。！？!?～~. ")
        tail = str(self.persona.get("tail", "") or "")
        greeting = str(self.persona.get("greeting", "") or "")
        # 避免重复标点/重复尾缀
        out = text + tail
        return greeting + out if greeting and not out.startswith(greeting) else out

    async def egress(self, reply: str, sess, channel: str) -> Dict[str, Any]:
        """按渠道路由回复（bili/webui/cli/api）。"""
        text = self.polish(reply, sess)
        ch = CHANNELS.get(channel) or CHANNELS.get(self._default_channel)
        try:
            await ch.egress(text, sess)
            return {"success": True, "reply": text, "channel": channel}
        except Exception as e:
            logger.error(f"[StreamerFront] egress 失败({channel}): {e}")
            return {"success": False, "error": str(e), "reply": text}


# ---------- 模块级单例 ----------
_front: Optional[StreamerFront] = None


def get_streamer_front() -> StreamerFront:
    global _front
    if _front is None:
        _front = StreamerFront()
    return _front


async def streamer_ingress(msg: StreamerMessage, sessions: SessionManager) -> Dict[str, Any]:
    """inbox 在 STREAMER_NEW=1 时调用的门面入口（供 inbox.submit 复用）。"""
    return await get_streamer_front().ingress(msg, sessions)
