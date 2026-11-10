# ============================================================
# LLM_v2/io/bilibili_emitter.py — Bilibili 弹幕 → 事件发射器
# ============================================================
# 桥接现有 LLM/bili/bili_listener.py 到 V2 EventBus
# 把弹幕/礼物/SC 都转为 V2 标准事件
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from LLM_v2.core.event_bus import EventBus
from LLM_v2.events import EVENTS

logger = logging.getLogger(__name__)


class BilibiliEmitter:
    """Bilibili 监听器的事件桥接器

    用法：
        emitter = BilibiliEmitter(runtime.bus)
        await emitter.start()    # 自动起 BiliListener + 桥接
        ...
        await emitter.stop()
    """

    def __init__(
        self,
        bus: EventBus,
        room_ids=None,
        sessdata: str = "",
        output_port: int = 8081,
        forward_http: bool = False,
        enable_filter: bool = True,  # 是否启用弹幕过滤
    ):
        self.bus = bus
        self.room_ids = room_ids
        self.sessdata = sessdata
        self.output_port = output_port
        self.forward_http = forward_http
        self.enable_filter = enable_filter
        self._listener = None
        self._task: Optional[asyncio.Task] = None

        # 状态统计（迁移自 broadcast/bilibili_state.py）
        self._stats = {
            "danmaku_count": 0,
            "gift_count": 0,
            "sc_count": 0,
            "filtered_count": 0,
            "session_start": None,
        }
        # 弹幕过滤器（迁移自 broadcast/danmu_filter.py 简化版）
        self._filter_keywords_block = ["广告", "禁言", "赌博"]
        self._filter_keywords_slow = ["签到"]

    async def start(self):
        """启动 BiliListener 并桥接到 EventBus"""
        try:
            from LLM.bili.bili_listener import BiliListener
        except ImportError as e:
            logger.error(f"[bili-emitter] 无法导入 BiliListener: {e}")
            return False

        # 创建 BiliListener，注入我们的 on_message 回调
        self._listener = BiliListener(
            room_ids=self.room_ids,
            sessdata=self.sessdata,
            output_port=self.output_port,
            forward=self.forward_http,
        )
        # 替换它的 on_message 回调
        self._listener._on_message = self._on_message  # type: ignore

        self._stats["session_start"] = asyncio.get_event_loop().time()

        # 在后台协程里跑 BiliListener（它内部用 asyncio.run）
        self._task = asyncio.create_task(self._run_listener())
        logger.info(f"[bili-emitter] started, rooms={self._listener.room_ids}")
        return True

    async def _run_listener(self):
        """在协程中跑 BiliListener.start()"""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._listener.start)
        except Exception as e:
            logger.exception(f"[bili-emitter] listener crashed: {e}")

    async def stop(self):
        """停止监听"""
        if self._listener:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._listener.stop)
            except Exception as e:
                logger.warning(f"[bili-emitter] stop 异常: {e}")
        if self._task:
            self._task.cancel()
        logger.info("[bili-emitter] stopped")

    # ---------- 事件桥接 ----------

    async def _on_message(self, openai_msg: dict, raw: dict):
        """BiliListener 回调：转 EventBus 事件"""
        mtype = raw.get("type", "danmaku")
        user = raw.get("user", {})
        uname = user.get("uname", "匿名")

        # 弹幕过滤（迁移自 broadcast/danmu_filter.py）
        if self.enable_filter and mtype == "danmaku":
            text = raw.get("content", "")
            if any(kw in text for kw in self._filter_keywords_block):
                logger.debug(f"[bili-emitter] 过滤弹幕: {text[:30]}")
                self._stats["filtered_count"] += 1
                return
            # 慢速关键词（限流）
            if any(kw in text for kw in self._filter_keywords_slow):
                await asyncio.sleep(2)

        if mtype == "danmaku":
            text = raw.get("content", "")
            payload = {"user": uname, "text": text, "uid": user.get("uid")}
            logger.debug(f"[bili-emitter] 弹幕: {uname}: {text[:30]}")
            self._stats["danmaku_count"] += 1
            await self.bus.emit(EVENTS.BILIBILI_MSG, payload)

        elif mtype == "gift":
            g = raw.get("gift", {})
            payload = {
                "user": uname,
                "gift": g.get("name", "礼物"),
                "num": g.get("num", 1),
                "price": g.get("price", 0),
                "uid": user.get("uid"),
            }
            logger.info(f"[bili-emitter] 礼物: {uname} -> {g.get('name')}x{g.get('num')}")
            self._stats["gift_count"] += 1
            await self.bus.emit(EVENTS.GIFT_RECEIVED, payload)

        elif mtype == "super_chat":
            payload = {
                "user": uname,
                "text": raw.get("message", ""),
                "price": raw.get("price", 0),
                "uid": user.get("uid"),
            }
            logger.info(f"[bili-emitter] SC: {uname} -> {raw.get('message')}")
            self._stats["sc_count"] += 1
            await self.bus.emit(EVENTS.GIFT_RECEIVED, payload)  # SC 也归类为礼物

    def get_stats(self) -> dict:
        """返回会话统计（迁移自 broadcast/bilibili_state.py）"""
        return dict(self._stats)


__all__ = ['BilibiliEmitter']
