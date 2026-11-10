#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 引擎：edge-tts（微软 Edge 免费语音，真流式）

- 零成本、免鉴权、在线。参考 NagaAgent voice/output/tts_handler.py。
- edge_tts.Communicate.stream() 原生逐块 yield 音频（mp3），本引擎转 PCM 16bit mono。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import AsyncIterator, Dict, Optional

from audio.tts_engine import AudioChunk, AudioResult, TTSEngine, register_engine

logger = logging.getLogger(__name__)

try:
    import edge_tts
    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False

try:
    import pydub
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False


class EdgeTTSEngine(TTSEngine):
    name = "edge_tts"
    display_name = "Edge TTS (免费流式)"

    def __init__(self, voice: str = ""):
        self.voice = voice or os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")

    def available(self) -> bool:
        return EDGE_AVAILABLE

    @staticmethod
    def _mp3_to_pcm16mono(mp3_bytes: bytes, sample_rate: int = 24000) -> bytes:
        """mp3 -> PCM int16 mono（用于声卡播放）。"""
        if not PYDUB_AVAILABLE:
            return mp3_bytes  # 无 pydub 时原样透传（播放器需支持 mp3，兜底）
        seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
        seg = seg.set_channels(1).set_frame_rate(sample_rate)
        return seg.raw_data

    async def astream(self, text: str, *, tone: str = "",
                      ref_audio: str = "", **kw) -> AsyncIterator[AudioChunk]:
        if not EDGE_AVAILABLE:
            return
        if not PYDUB_AVAILABLE:
            # 缺 pydub 无法把 edge 的 MP3 转成声卡可播的 PCM，放弃流式（交由上层回退路径）
            logger.warning("[EdgeTTS] 未安装 pydub，无法将 MP3 转 PCM，跳过流式")
            return
        comm = edge_tts.Communicate(text=text, voice=self.voice)
        # edge_tts 原生逐块 yield 的是 MP3 分片，单片并非独立 MP3 帧、逐个解码不可靠；
        # 故先收齐整段 MP3，再一次性解码为 PCM16 mono 交给声卡（保证音质正确）。
        mp3 = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data") or b""
                if data:
                    mp3.extend(data)
        if not mp3:
            return
        pcm = self._mp3_to_pcm16mono(bytes(mp3), 24000)
        if pcm:
            yield AudioChunk(data=pcm, sample_rate=24000,
                             meta={"engine": self.name})

    async def synthesize(self, text: str, *, tone: str = "",
                         ref_audio: str = "", **kw) -> AudioResult:
        chunks = b""
        async for c in self.astream(text, tone=tone, **kw):
            chunks += c.data
        if not chunks:
            return AudioResult(success=False, error="edge-tts 未产出音频", engine=self.name)
        return AudioResult(success=True, audio_data=chunks, sample_rate=24000,
                           format="pcm", engine=self.name)


register_engine(EdgeTTSEngine)
