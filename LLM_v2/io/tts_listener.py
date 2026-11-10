# ============================================================
# LLM_v2/io/tts_listener.py — TTS 事件监听器
# ============================================================
# on TTS_SPEAK 事件 → 调 audio.tts_engine.TTSEngine.astream() → 播放
# 不阻塞主流程（流式后台播放）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from LLM_v2.core.event_bus import EventBus
from LLM_v2.events import EVENTS

logger = logging.getLogger(__name__)


class TTSListener:
    """TTS 事件监听器

    监听 TTS_SPEAK 事件，调用 TTSEngine 合成并播放音频。
    """

    def __init__(self, bus: EventBus, engine=None, player=None):
        self.bus = bus
        self._engine = engine  # TTSEngine 实例（懒加载）
        self._player = player  # 播放器（懒加载）
        self._setup()

    def _setup(self):
        """懒加载 TTS 引擎（避免循环依赖）"""
        try:
            from audio.tts_engine import get_engine
            self._engine = self._engine or get_engine("auto")
        except ImportError as e:
            logger.warning(f"[tts-listener] TTS 引擎未加载: {e}")
        except Exception as e:
            logger.warning(f"[tts-listener] TTS 引擎初始化失败: {e}")

    async def start(self):
        """注册监听器"""
        self.bus.on(EVENTS.TTS_SPEAK, self._on_speak)
        logger.info("[tts-listener] started")

    async def _on_speak(self, payload: Any):
        """处理 TTS_SPEAK 事件"""
        if not isinstance(payload, dict):
            return
        text = payload.get("text", "")
        mood = payload.get("mood", "neutral")
        tone = payload.get("tone")

        if not text:
            return

        logger.info(f"[tts-listener] TTS: {text[:50]} (mood={mood})")

        if self._engine is None:
            logger.warning("[tts-listener] 无 TTS 引擎，跳过播放")
            return

        # 后台播放（不阻塞 listener）
        asyncio.create_task(self._synthesize_and_play(text, tone))

    async def _synthesize_and_play(self, text: str, tone: Optional[str]):
        """合成 + 播放（流式）"""
        try:
            loop = asyncio.get_running_loop()
            # TTS 是 IO 密集 + CPU 密集，放到默认 executor
            chunks = []
            async for chunk in self._engine.astream(text):
                chunks.append(chunk)
                # 实时播放（v1 简化：先收集）
            logger.debug(f"[tts-listener] 合成完成, chunks={len(chunks)}")
            # TODO: 接入实际播放器（pygame / sounddevice）
        except Exception as e:
            logger.exception(f"[tts-listener] 合成失败: {e}")


__all__ = ['TTSListener']
