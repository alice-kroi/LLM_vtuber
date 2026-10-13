#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播脑统一收件箱。

阶段1：标准化消息统一入口 → 转 OpenAI 格式 → 喂旧 LangGraph 链路
（legacy_deliver 由 main.py 注入，即 process_openai_messages）。LangGraph 并行保留。
阶段2：改为三脑消息链（主播脑门面 ingress）。

双写开关：环境变量 STREAMER_NEW != "0" 时走新链路（阶段2）；默认 "0" 走旧链路。

事件 trigger 语义（对齐可提/Cortico，投递时机控制）：
- flush（默认）   ：立即投递，并带上面积压的 debounce/piggyback 消息
- preempt         ：立即投递（保留语义；真正"打断当前模型调用"需脑层配合，暂同 flush）
- debounce        ：进入积压缓冲，安静窗口（默认 1.5s）后合批投递（弹幕风暴场景）
- piggyback       ：仅排队，随下一次 flush/preempt 一起投递（状态快照等低优先级）
- deliver=False   ：仅存储（回录/状态记录），不投递、不打扰主播脑
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from LLM.streamer.message import StreamerMessage
from LLM.streamer.session import SessionManager

logger = logging.getLogger(__name__)

LegacyDeliver = Callable[[List[dict]], Awaitable[Any]]

# 弹幕等外部消息的安静窗口（秒）：窗口内继续来消息则重置计时，攒批投递
DEBOUNCE_SECONDS = float(os.environ.get("STREAMER_DEBOUNCE_SECONDS", "1.5"))
STORE_LIMIT = 200


class StreamerInbox:
    """统一收件箱：submit 标准化消息，按开关路由旧/新链路，支持 trigger 投递语义。"""

    def __init__(self, legacy_deliver: Optional[LegacyDeliver] = None):
        self._legacy_deliver = legacy_deliver
        self.sessions = SessionManager()
        self._use_new = os.environ.get("STREAMER_NEW", "0") != "0"
        # trigger 语义状态
        self._pending: List[StreamerMessage] = []       # debounce/piggyback 积压
        self._stored: List[StreamerMessage] = []        # deliver=False 的记录
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def use_new(self) -> bool:
        return self._use_new

    def bind_legacy(self, deliver: LegacyDeliver) -> None:
        """绑定旧链路投递回调（main.py 注入 process_openai_messages）。"""
        self._legacy_deliver = deliver

    # ---------- 实际投递 ----------

    async def _do_deliver(self, msgs: List[StreamerMessage]) -> Dict[str, Any]:
        if not msgs:
            return {"success": True, "delivered": 0}
        if self._use_new:
            from LLM.streamer.streamer_brain import streamer_ingress
            results = []
            for m in msgs:
                try:
                    results.append(await streamer_ingress(m, self.sessions))
                except Exception as e:
                    logger.warning(f"[Inbox] streamer_ingress 失败: {e}")
            return {"success": True, "delivered": len(results),
                    "batch": [m.content[:40] for m in msgs], "results": results}
        # 阶段1：旧链路
        if self._legacy_deliver is None:
            return {"success": False, "error": "inbox 未绑定 legacy_deliver"}
        r = await self._legacy_deliver([m.to_openai_dict() for m in msgs])
        return {"success": True, "delivered": len(msgs),
                "batch": [m.content[:40] for m in msgs], "legacy": r}

    async def _flush_pending(self) -> Dict[str, Any]:
        """取出积压并投递（debounce 合批的落点）。"""
        async with self._lock:
            if not self._pending:
                return {"success": True, "delivered": 0}
            batch, self._pending = self._pending, []
        try:
            return await self._do_deliver(batch)
        except Exception as e:
            logger.warning(f"[Inbox] 积压投递失败: {e}")
            return {"success": False, "error": str(e)}

    async def _debounce_flusher(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self._flush_pending()

    # ---------- 统一入口 ----------

    async def submit(self, msg: StreamerMessage, trigger: str = "flush",
                     deliver: bool = True) -> Dict[str, Any]:
        """渠道消息统一入口（trigger 语义见模块 docstring）。

        默认 trigger="flush"：与旧行为完全一致（立即投递），零回归。
        弹幕/低优先级渠道可显式传 trigger="debounce"/"piggyback" 合批。
        """
        if not deliver:
            # 仅存储（回录/状态快照），不投递、不打扰主播脑
            async with self._lock:
                self._stored.append(msg)
                if len(self._stored) > STORE_LIMIT:
                    self._stored = self._stored[-STORE_LIMIT:]
            return {"success": True, "delivered": False, "stored": True,
                    "content": msg.content[:40]}

        if trigger in ("flush", "preempt"):
            # 立即投递；顺带清空积压（piggyback 随本次批次走）
            if self._pending:
                async with self._lock:
                    batch, self._pending = self._pending, []
                batch.append(msg)
                return await self._do_deliver(batch)
            return await self._do_deliver([msg])

        if trigger == "debounce":
            async with self._lock:
                self._pending.append(msg)
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._debounce_flusher())
            return {"success": True, "queued": True, "trigger": "debounce"}

        if trigger == "piggyback":
            async with self._lock:
                self._pending.append(msg)
            return {"success": True, "queued": True, "trigger": "piggyback"}

        raise ValueError(f"未知 trigger: {trigger}（flush/preempt/debounce/piggyback）")

    def recent(self, limit: int = 20) -> List[StreamerMessage]:
        """最近投递/存储的消息（供 /api/messages 等读取）。"""
        return list(self._stored[-limit:])


# 模块级单例：main.py 初始化时 bind_legacy，webui/api 等渠道用 get_inbox() 提交
_inbox: Optional[StreamerInbox] = None


def init_inbox(legacy_deliver: Optional[LegacyDeliver] = None) -> StreamerInbox:
    global _inbox
    if _inbox is None:
        _inbox = StreamerInbox()
    if legacy_deliver is not None:
        _inbox.bind_legacy(legacy_deliver)
    return _inbox


def get_inbox() -> Optional[StreamerInbox]:
    return _inbox
