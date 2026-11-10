# ============================================================
# LLM_v2/io/live2d_listener.py — Live2D 事件监听器
# ============================================================
# on LIVE2D_EXPRESSION 事件 → VTubeStudioAPI 切换表情
# on LIVE2D_MOTION 事件 → 触发动作
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from LLM_v2.core.event_bus import EventBus
from LLM_v2.events import EVENTS

logger = logging.getLogger(__name__)


class Live2DListener:
    """Live2D 事件监听器

    监听 LIVE2D_EXPRESSION / LIVE2D_MOTION 事件，通过 VTubeStudioAPI 控制 Live2D 模型。
    """

    # ALMA 八象限+neutral → Live2D expression ID（与 persona.yaml avatar.emotion_to_expression 对齐）
    QUADRANT_TO_EXPRESSION = {
        "exuberant": "smile_01",     # +P +A +D
        "dependent": "smile_02",     # +P +A -D
        "relaxed": "calm_01",        # +P -A +D
        "docile": "calm_01",         # +P -A -D
        "hostile": "angry_01",       # -P +A +D
        "anxious": "sad_01",         # -P +A -D
        "disdainful": "angry_01",    # -P -A +D
        "boredom": "neutral_01",     # -P -A -D
        "neutral": "neutral_01",
        # 旧 7 值兜底（外部仍可能发旧事件）
        "excited": "smile_01",
        "calm": "calm_01",
        "angry": "angry_01",
        "sad": "sad_01",
        "confident": "smile_02",
        "submissive": "sad_02",
    }

    def __init__(self, bus: EventBus, vts_api=None, persona=None):
        self.bus = bus
        self._vts = vts_api  # VTubeStudioAPI 实例（懒加载）
        self._persona = persona  # PersonaProfile（懒加载）
        self._last_expression = None
        self._last_expression_at = 0
        self._debounce_sec = 1.0  # 表情防抖
        self._setup()

    def _setup(self):
        """懒加载 VTubeStudioAPI"""
        try:
            from live2d.vtuber_studio_info import VTubeStudioAPI
            if self._vts is None:
                self._vts = VTubeStudioAPI()
        except ImportError as e:
            logger.warning(f"[live2d-listener] VTS API 未加载: {e}")

    async def start(self):
        """注册监听器"""
        self.bus.on(EVENTS.LIVE2D_EXPRESSION, self._on_expression)
        self.bus.on(EVENTS.LIVE2D_MOTION, self._on_motion)
        logger.info("[live2d-listener] started")

    async def _on_expression(self, payload: Any):
        """处理 LIVE2D_EXPRESSION 事件"""
        import time
        now = time.time()
        # 防抖
        if now - self._last_expression_at < self._debounce_sec:
            return
        self._last_expression_at = now

        if not isinstance(payload, dict):
            return
        quadrant = payload.get("quadrant", "neutral")
        expression_id = self._resolve_expression(quadrant)

        # 去重
        if expression_id == self._last_expression:
            return
        self._last_expression = expression_id

        logger.info(f"[live2d-listener] 切表情: {quadrant} -> {expression_id}")
        await self._set_expression(expression_id)

    async def _on_motion(self, payload: Any):
        """处理 LIVE2D_MOTION 事件"""
        if not isinstance(payload, dict):
            return
        motion_id = payload.get("motion_id", "")
        if not motion_id:
            return
        logger.info(f"[live2d-listener] 触发动作: {motion_id}")
        await self._play_motion(motion_id)

    def _resolve_expression(self, quadrant: str) -> str:
        """解析 quadrant → expression_id（优先用 persona 配置）"""
        if self._persona and self._persona.avatar.emotion_to_expression:
            custom = self._persona.avatar.emotion_to_expression.get(quadrant)
            if custom:
                return custom
        return self.QUADRANT_TO_EXPRESSION.get(quadrant, "neutral_01")

    async def _set_expression(self, expression_id: str):
        """调 VTS API 切表情（VTubeStudioAPI.activate_expression 是 async）"""
        if self._vts is None:
            logger.debug("[live2d-listener] 无 VTS，跳过")
            return
        try:
            await self._vts.activate_expression(expression_id, True, 0.25)
        except Exception as e:
            logger.warning(f"[live2d-listener] 切表情失败: {e}")

    async def _play_motion(self, motion_id: str):
        """调 VTS API 触发动作（VTubeStudioAPI.trigger_hotkey 是 async）"""
        if self._vts is None:
            return
        try:
            await self._vts.trigger_hotkey(motion_id, None)
        except Exception as e:
            logger.warning(f"[live2d-listener] 触发动作失败: {e}")


__all__ = ['Live2DListener']
