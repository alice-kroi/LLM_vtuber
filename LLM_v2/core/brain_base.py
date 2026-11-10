# ============================================================
# LLM_v2/core/brain_base.py — 三脑共享基类
# ============================================================
# 每个 brain 都是独立的 asyncio 协程
# 共享同一个 PersonaRuntime（Persona + State）
# 通过 EventBus 互相通信
# ============================================================

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from LLM_v2.core.persona_runtime import PersonaRuntime
from LLM_v2.core.event_bus import EventBus

logger = logging.getLogger(__name__)


class Brain(ABC):
    """三脑共享基类

    关键设计：
        - 每个 brain 是独立 asyncio 协程（run()）
        - 共享同一个 PersonaRuntime
        - 通过 self.bus 注册监听器 + 发射事件
        - 状态变更通过 self.apply_event() 集中入口
    """

    def __init__(
        self,
        name: str,
        runtime: PersonaRuntime,
        heartbeat_interval: float = 5.0,
    ):
        self.name = name
        self.runtime = runtime
        self.bus: EventBus = runtime.bus
        self.heartbeat_interval = heartbeat_interval
        self.system_prompt: str = ""
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ---------- 抽象方法（子类必须实现）----------

    @abstractmethod
    async def setup(self) -> None:
        """注册该 brain 的事件监听器"""
        raise NotImplementedError

    async def heartbeat(self) -> None:
        """定期任务（子类重写）"""
        pass

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动该 brain 协程"""
        if self._running:
            logger.warning(f"[brain:{self.name}] already running")
            return
        self._running = True
        self._task = asyncio.create_task(self.run(), name=f"brain-{self.name}")
        logger.info(f"[brain:{self.name}] started")

    async def stop(self) -> None:
        """停止该 brain"""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"[brain:{self.name}] stopped")

    async def run(self) -> None:
        """主循环（永不停止）"""
        await self.setup()
        logger.info(f"[brain:{self.name}] entering main loop")
        while self._running:
            try:
                await self.heartbeat()
            except Exception as e:
                logger.exception(f"[brain:{self.name}] heartbeat error: {e}")
            await asyncio.sleep(self.heartbeat_interval)

    # ---------- 工具方法 ----------

    async def emit(self, event_type: str, payload: Any = None) -> None:
        """发射事件"""
        await self.bus.emit(event_type, payload)

    def apply_event(self, event_type: str, **kwargs) -> None:
        """统一的状态变更入口"""
        self.runtime.apply_event(event_type, **kwargs)

    def on(self, event_type: str, listener):
        """注册监听器（语法糖）"""
        return self.bus.on(event_type, listener)

    # ---------- 诊断 ----------

    def __repr__(self) -> str:
        return (
            f"<Brain name={self.name!r} "
            f"heartbeat={self.heartbeat_interval}s "
            f"running={self._running}>"
        )


__all__ = ['Brain']
