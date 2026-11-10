# ============================================================
# LLM_v2/domains/tts/tts.py — TTS 语音能力域
# ============================================================
# 将 V1 的 LLM_v2/io/tts_listener.py（事件监听器）升级为能力域：
#   - 类型：Actuator（提供工具 + 监听 TTS_SPEAK 事件）
#   - 工具：tts_status / tts_speak / tts_stop（避开 _STREAMER_RESERVED 保留字 tts_synthesize/play_audio）
#   - 事件：outbound 监听 EVENTS.TTS_SPEAK → 合成并播放
#   - 生命周期：start() 注册监听 + 懒加载引擎；stop() 注销监听
#   - default_enabled=False（默认不启动，作为能力挂载，可由 WebUI/配置热启）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    CapabilityDomain,
    Actuator,
    CapabilityType,
    tool_schema,
)

logger = logging.getLogger(__name__)

TTS_TOOL_NAMES = [
    "tts_status",
    "tts_speak",
    "tts_stop",
]

TTS_TOOLS_SCHEMA: list = [
    tool_schema(
        "tts_status",
        "查询当前 TTS 引擎可用性 / 播放状态。",
    ),
    tool_schema(
        "tts_speak",
        "合成并播放一段语音（指定文本；可选语气 tone）。",
        {
            "text": {"type": "string", "description": "要朗读的文本"},
            "tone": {"type": "string", "description": "可选语气（happy/sad/neutral...），默认空"},
        },
        required=["text"],
    ),
    tool_schema(
        "tts_stop",
        "停止当前正在播放的语音。",
    ),
]


_TTS_PROMPT = """可用工具（TTS 语音能力）：
- tts_status: 查询 TTS 引擎状态
- tts_speak: 合成并播放语音（text 必填）
- tts_stop: 停止播放

执行原则：
1. 需要"说话"时用 tts_speak；确认引擎时用 tts_status。
2. 本域默认不启动，未启用时返回 domain_disabled。
3. 合成播放为流式（边收边播），不阻塞主流程。
"""


class TTSDomain(Actuator):
    """TTS 语音能力域（H-2 Actuator 类型）

    事件链路（V1 tts_listener 迁移）：
        StreamerBrain --emit TTS_SPEAK--> 本域 _on_tts_speak --引擎合成--> 播放
    """

    id = "tts"
    label = "TTS 语音合成与播放"
    prefix = "tts_"
    default_enabled = False      # 默认不启动（能力挂载，可由 WebUI/配置热启）
    type = CapabilityType.ACTUATOR

    call_timeout = 120.0

    def __init__(self):
        super().__init__()
        self._engine = None       # 懒加载 TTSEngine
        self._playing = False
        self.bus = None           # 由 DomainAssembly.start_domain 注入

    # ---------- H-2 事件声明 ----------

    def inbound_event_types(self) -> List[str]:
        """本域不产出新事件"""
        return []

    def outbound_event_types(self) -> List[str]:
        """本域监听的事件：TTS_SPEAK"""
        from LLM_v2.events import EVENTS
        return [EVENTS.TTS_SPEAK]

    # ---------- 工具 ----------

    def allowed_names(self) -> List[str]:
        return list(TTS_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in TTS_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _TTS_PROMPT

    # ---------- 生命周期 ----------

    def preflight(self) -> Optional[str]:
        """前置检查：只查硬依赖（audio.tts_engine 模块可导入）。

        引擎可用性（本地服务/API key）是运行态依赖，不阻塞域挂载：
        域 start 后监听器即生效，引擎懒加载，tts_speak 调用时才校验。
        """
        try:
            import audio.tts_engine  # noqa: F401
            return None
        except Exception as e:
            return f"audio.tts_engine 不可导入: {e}"

    async def start(self) -> None:
        """注册 TTS_SPEAK 监听器 + 懒加载引擎"""
        from LLM_v2.events import EVENTS
        if self.bus is not None:
            self.bus.on(EVENTS.TTS_SPEAK, self._on_tts_speak)
            logger.info(f"[tts-domain] 已注册 {EVENTS.TTS_SPEAK} 监听器")
        self._load_engine()
        logger.info("[tts-domain] started")

    async def stop(self) -> None:
        """注销监听器 + 停止播放"""
        from LLM_v2.events import EVENTS
        if self.bus is not None:
            try:
                self.bus.off(EVENTS.TTS_SPEAK, self._on_tts_speak)
            except Exception:
                pass
        self._playing = False
        logger.info("[tts-domain] stopped")

    # ---------- 内部：引擎 ----------

    def _load_engine(self):
        if self._engine is not None:
            return
        try:
            # 先触发引擎包注册（audio/engines/__init__.py load_all()）
            import audio.engines  # noqa: F401
            from audio.tts_engine import resolve_engine
            self._engine = resolve_engine("auto")
            if self._engine:
                logger.info(f"[tts-domain] 引擎就绪: {self._engine.name}")
            else:
                logger.warning("[tts-domain] 无可用引擎")
        except Exception as e:
            logger.warning(f"[tts-domain] 引擎加载失败: {e}")

    # ---------- 内部：事件监听 ----------

    async def _on_tts_speak(self, payload: Any):
        """TTS_SPEAK 事件 → 合成并播放"""
        if not isinstance(payload, dict):
            return
        text = payload.get("text", "")
        if not text:
            return
        tone = payload.get("tone", "")
        logger.info(f"[tts-domain] TTS_SPEAK: {text[:50]} (tone={tone})")
        await self.tts_speak(text=text, tone=tone)

    # ---------- 工具实现 ----------

    async def tts_status(self) -> Dict[str, Any]:
        """查询引擎与播放状态"""
        if self._engine is None:
            self._load_engine()
        eng = self._engine
        return {
            "success": True,
            "engine": eng.describe() if eng else None,
            "playing": self._playing,
            "enabled": self.id in getattr(self, "_active", set()) or True,
        }

    async def tts_speak(self, text: str, tone: str = "") -> Dict[str, Any]:
        """合成并播放语音（流式：边收边播）"""
        if not text or not text.strip():
            return {"success": False, "error": "text 为空"}
        if self._engine is None:
            self._load_engine()
        if self._engine is None:
            return {"success": False, "error": "无可用 TTS 引擎"}

        # 流式播放（不阻塞调用方）
        self._playing = True
        asyncio.create_task(self._play_task(text, tone))
        return {"success": True, "status": "started", "text": text[:50]}

    async def tts_stop(self) -> Dict[str, Any]:
        """停止播放"""
        self._playing = False
        return {"success": True, "playing": False}

    async def _play_task(self, text: str, tone: str):
        """后台播放任务：引擎合成 → 流式播放器"""
        try:
            from audio.stream_player import play_pcm_stream
            from audio.tts_engine import AudioChunk

            chunks = []

            async def _iter():
                async for c in self._engine.astream(text, tone=tone):
                    chunks.append(c)
                    yield c

            result = await play_pcm_stream(_iter())
            self._playing = False
            logger.info(f"[tts-domain] 播放完成: duration={result.get('duration', 0):.1f}s "
                        f"chunks={result.get('chunks', 0)}")
        except Exception as e:
            self._playing = False
            logger.exception(f"[tts-domain] 播放失败: {e}")


def build_tts_domain() -> TTSDomain:
    return TTSDomain()


__all__ = ['TTSDomain', 'build_tts_domain', 'TTS_TOOL_NAMES', 'TTS_TOOLS_SCHEMA']
