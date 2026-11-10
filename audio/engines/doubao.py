#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 引擎：豆包云 TTS（V3 HTTP 单向流式）

- 复用 audio/cloud_tts.py 的配置/请求逻辑，但 astream 逐帧产出 base64 音频块（真流式）。
- 认证：X-Api-Key（环境变量 TTS_KEY / DOUBAO_API_KEY）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator, Dict, Optional

from audio.tts_engine import AudioChunk, AudioResult, TTSEngine, register_engine

logger = logging.getLogger(__name__)

try:
    import aiohttp
    AIOHTTP = True
except ImportError:
    AIOHTTP = False

V3_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def _api_key() -> str:
    return os.environ.get("TTS_KEY") or os.environ.get("DOUBAO_API_KEY") or os.environ.get("VOLCENGINE_API_KEY") or ""


def _voice_type() -> str:
    return os.environ.get("DOUBAO_TTS_VOICE", "zh_female_vv_uranus_bigtts")


def _resource_id() -> str:
    return os.environ.get("DOUBAO_TTS_RESOURCE", "seed-tts-2.0")


class DoubaoEngine(TTSEngine):
    name = "doubao"
    display_name = "豆包云 TTS (V3 流式)"

    def __init__(self, api_url: str = "", voice: str = "", resource_id: str = ""):
        self.api_url = api_url or V3_ENDPOINT
        self.voice = voice or _voice_type()
        self.resource_id = resource_id or _resource_id()

    def available(self) -> bool:
        return AIOHTTP and bool(_api_key())

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": _api_key(),
            "X-Api-Resource-Id": self.resource_id,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }

    def _payload(self, text: str, sample_rate: int = 24000) -> Dict:
        return {
            "req_params": {
                "text": text,
                "speaker": self.voice,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": sample_rate,
                    "speech_rate": 0,
                    "loudness_rate": 0,
                },
            },
        }

    async def astream(self, text: str, *, tone: str = "",
                      ref_audio: str = "", **kw) -> AsyncIterator[AudioChunk]:
        """豆包 V3 单向流式：每帧 JSON {data: base64_pcm}，逐帧 yield PCM 块。"""
        payload = self._payload(text)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, json=payload, headers=self._headers()) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"[doubao] HTTP {resp.status}: {body[:200]}")
                    return
                buf = ""
                decoder = json.JSONDecoder()
                async for raw in resp.content.iter_chunked(4096):
                    buf += raw.decode("utf-8", errors="ignore")
                    while buf:
                        try:
                            obj, end = decoder.raw_decode(buf.lstrip())
                        except json.JSONDecodeError:
                            break
                        buf = buf[end:].lstrip()
                        code = obj.get("code", -1)
                        if code != 0:
                            logger.error(f"[doubao] API code={code}: {obj.get('message','')}")
                            return
                        data = obj.get("data")
                        if data:
                            try:
                                yield AudioChunk(data=base64.b64decode(data),
                                                 sample_rate=obj.get("audio_params", {}).get("sample_rate", 24000),
                                                 meta={"engine": self.name})
                            except Exception as e:
                                logger.warning(f"[doubao] base64 解码失败: {e}")
                        if obj.get("code") == 20000000:  # 合成完成标记
                            return

    async def synthesize(self, text: str, *, tone: str = "",
                         ref_audio: str = "", **kw) -> AudioResult:
        """非流式：收集所有音频块拼成 PCM。"""
        chunks = []
        rate = 24000
        async for c in self.astream(text, tone=tone, **kw):
            chunks.append(c.data)
            rate = c.sample_rate
        if not chunks:
            return AudioResult(success=False, error="未收到豆包音频", engine=self.name)
        return AudioResult(success=True, audio_data=b"".join(chunks),
                           sample_rate=rate, format="pcm", engine=self.name)


register_engine(DoubaoEngine)
