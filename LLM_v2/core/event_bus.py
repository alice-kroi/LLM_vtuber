# ============================================================
# LLM_v2/core/event_bus.py — 事件总线（EDA 核心）
# ============================================================
# 抛弃 LangGraph，采用 asyncio 事件驱动架构
# 核心职责：
#   1. 注册监听器（on）
#   2. 发射事件（emit）
#   3. 异步分发（dispatcher）
#   4. 错误隔离（单 listener 失败不影响其他）
# ============================================================

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Optional
from weakref import WeakSet

logger = logging.getLogger(__name__)


# 类型：监听器签名
Listener = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    """异步事件总线

    用法：
        bus = EventBus()
        await bus.start()

        async def on_chat(payload):
            print("收到:", payload)
        bus.on("bilibili_msg", on_chat)

        await bus.emit("bilibili_msg", {"text": "hello"})
    """

    def __init__(self, name: str = "default", queue_size: int = 0):
        self.name = name
        self.listeners: Dict[str, List[Listener]] = defaultdict(list)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._running = False
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._stats = {
            "emitted": 0,
            "dispatched": 0,
            "errors": 0,
            "by_type": defaultdict(int),
        }
        # 追踪正在执行的协程（用于优雅关闭）
        self._pending_tasks: WeakSet = WeakSet()

    # ---------- 注册 ----------

    def on(self, event_type: str, listener: Listener) -> Listener:
        """注册监听器

        Args:
            event_type: 事件类型
            listener: async 函数，签名为 async def(payload) -> None

        Returns:
            原 listener（支持装饰器用法）
        """
        self.listeners[event_type].append(listener)
        logger.debug(f"[bus:{self.name}] on({event_type}) +1 listener "
                     f"(total={len(self.listeners[event_type])})")
        return listener

    def off(self, event_type: str, listener: Listener) -> bool:
        """移除监听器"""
        try:
            self.listeners[event_type].remove(listener)
            logger.debug(f"[bus:{self.name}] off({event_type}) -1 listener")
            return True
        except ValueError:
            return False

    def listener_count(self, event_type: str) -> int:
        return len(self.listeners.get(event_type, []))

    # ---------- 发射 ----------

    async def emit(self, event_type: str, payload: Any = None) -> None:
        """异步发射事件（推荐用法）"""
        self._stats["emitted"] += 1
        self._stats["by_type"][event_type] += 1
        await self.queue.put((event_type, payload, time.time()))

    def emit_nowait(self, event_type: str, payload: Any = None) -> bool:
        """非阻塞发射（队列满时丢弃，返回 False）"""
        try:
            self.queue.put_nowait((event_type, payload, time.time()))
            self._stats["emitted"] += 1
            self._stats["by_type"][event_type] += 1
            return True
        except asyncio.QueueFull:
            logger.warning(f"[bus:{self.name}] queue full, drop event {event_type}")
            return False

    # ---------- 启动 / 停止 ----------

    async def start(self) -> None:
        """启动分发器"""
        if self._running:
            logger.warning(f"[bus:{self.name}] already running")
            return
        self._running = True
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher(), name=f"bus-{self.name}-dispatcher"
        )
        logger.info(f"[bus:{self.name}] started")

    async def stop(self) -> None:
        """停止分发器（等待队列清空）"""
        if not self._running:
            return
        self._running = False
        # 等队列清空
        await self.queue.join()
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[bus:{self.name}] stopped")

    # ---------- 分发 ----------

    async def _dispatcher(self) -> None:
        """分发器主循环：从队列取事件，分发给所有 listener"""
        logger.debug(f"[bus:{self.name}] dispatcher loop start")
        while self._running:
            try:
                event_type, payload, ts = await self.queue.get()
                # 队列计数 -1（用于 join() 等待）
                self.queue.task_done()
                self._dispatch(event_type, payload, ts)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[bus:{self.name}] dispatcher error: {e}")
                self._stats["errors"] += 1
        logger.debug(f"[bus:{self.name}] dispatcher loop end")

    def _dispatch(self, event_type: str, payload: Any, ts: float) -> None:
        """实际分发：每个 listener 一个 task"""
        listeners = self.listeners.get(event_type, [])
        if not listeners:
            logger.debug(f"[bus:{self.name}] no listener for {event_type}")
            return
        for listener in listeners:
            task = asyncio.create_task(
                self._safe_invoke(listener, event_type, payload),
                name=f"listener-{event_type}-{listener.__name__}"
            )
            self._pending_tasks.add(task)
            self._stats["dispatched"] += 1

    async def _safe_invoke(self, listener: Listener, event_type: str, payload: Any) -> None:
        """安全调用：单 listener 失败不影响其他"""
        try:
            await listener(payload)
        except Exception as e:
            self._stats["errors"] += 1
            logger.exception(
                f"[bus:{self.name}] listener '{listener.__name__}' "
                f"failed on event '{event_type}': {e}"
            )

    # ---------- 诊断 ----------

    def stats(self) -> Dict[str, Any]:
        """返回总线统计信息"""
        return {
            **self._stats,
            "queue_size": self.queue.qsize(),
            "running": self._running,
            "listener_types": {
                k: len(v) for k, v in self.listeners.items()
            },
        }

    def __repr__(self) -> str:
        return (
            f"<EventBus name={self.name!r} "
            f"emitted={self._stats['emitted']} "
            f"dispatched={self._stats['dispatched']} "
            f"types={list(self.listeners.keys())}>"
        )


__all__ = ['EventBus', 'Listener']
