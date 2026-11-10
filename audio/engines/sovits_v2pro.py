#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 引擎：GPT-SoVITS v2pro（本地，流式）

- 服务：api_v2.py（D:/Github/GPT-SoVITS-v2pro-20250604），端口 9880
- 真流式：streaming_mode=3（定长块，响应最快）逐块 yield PCM/WAV 块
- 参考音频：按语气从 REF_AUDIO_DIR 选（项目已有 find_ref_audio_by_tone 逻辑，此处复用规则）
"""

from __future__ import annotations

import asyncio
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

# 默认指向项目内复制的 v2pro 服务（端口 9880）
DEFAULT_HOST = os.environ.get("SOVITS_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("SOVITS_PORT", "9880"))
# 参考音频目录（按语气选）；留空则用默认 ref_audio
REF_AUDIO_DIR = os.environ.get("SOVITS_REF_DIR", r"D:\Github\【GPT-SoVITS】爱莉希雅V2\参考音频")
DEFAULT_REF_AUDIO = os.environ.get("SOVITS_DEFAULT_REF", "archive_jingyuan_1.wav")


def _find_ref_by_tone(tone: str) -> str:
    """按语气挑选参考音频（复用项目 find_ref_audio_by_tone 的命名规则）。"""
    if not REF_AUDIO_DIR or not os.path.isdir(REF_AUDIO_DIR):
        return ""
    tone = (tone or "").strip()
    if tone:
        # 优先匹配【语气】开头的文件
        prefix = f"【{tone}】"
        for f in sorted(os.listdir(REF_AUDIO_DIR)):
            if f.startswith(prefix) and f.endswith(".wav"):
                return os.path.join(REF_AUDIO_DIR, f)
    # 兜底：任意 wav
    for f in sorted(os.listdir(REF_AUDIO_DIR)):
        if f.endswith(".wav"):
            return os.path.join(REF_AUDIO_DIR, f)
    return ""


class GPTSoVITS_v2proEngine(TTSEngine):
    name = "sovits_v2pro"
    display_name = "GPT-SoVITS v2pro (本地爱莉希雅)"

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port

    def available(self) -> bool:
        if not AIOHTTP:
            return False
        try:
            import socket
            with socket.create_connection((self.host, self.port), timeout=1.0):
                return True
        except Exception:
            return False

    def _payload(self, text: str, tone: str, ref_audio: str,
                 streaming_mode: int = 3) -> Dict:
        ref = ref_audio or _find_ref_by_tone(tone) or DEFAULT_REF_AUDIO
        return {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref,
            "aux_ref_audio_paths": [],
            "prompt_lang": "zh",
            "prompt_text": os.path.basename(ref).replace("【", "").replace("】", "").replace(".wav", ""),
            "top_k": 5, "top_p": 1.0, "temperature": 1.0,
            "text_split_method": "cut5", "batch_size": 1,
            "batch_threshold": 0.75, "split_bucket": True,
            "speed_factor": 1.0, "fragment_interval": 0.3, "seed": -1,
            "parallel_infer": False, "repetition_penalty": 1.35,
            "sample_steps": 32, "super_sampling": False,
            "streaming_mode": streaming_mode,
            "overlap_length": 2, "min_chunk_length": 16,
            "media_type": "wav",
        }

    async def astream(self, text: str, *, tone: str = "",
                      ref_audio: str = "", **kw) -> AsyncIterator[AudioChunk]:
        """真流式：streaming_mode=3（定长 PCM 块，边收边播）。"""
        url = f"http://{self.host}:{self.port}/tts"
        payload = self._payload(text, tone, ref_audio, streaming_mode=3)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"[sovits_v2pro] HTTP {resp.status}: {body[:200]}")
                    return
                # 逐块产出：v2pro 流式下首块带 WAV 头，后续为 raw PCM
                first = True
                async for chunk in resp.content.iter_chunked(8192):
                    if not chunk:
                        continue
                    yield AudioChunk(data=bytes(chunk),
                                     sample_rate=32000,
                                     is_wav_header=first and chunk[:4] == b"RIFF")
                    first = False

    async def synthesize(self, text: str, *, tone: str = "",
                         ref_audio: str = "", **kw) -> AudioResult:
        """非流式：streaming_mode=0，一次性返回完整 WAV。"""
        url = f"http://{self.host}:{self.port}/tts"
        payload = self._payload(text, tone, ref_audio, streaming_mode=0)
        timeout = aiohttp.ClientTimeout(total=120)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        return AudioResult(success=False, error=f"HTTP {resp.status}: {body[:200]}",
                                           engine=self.name)
                    data = await resp.read()
                    return AudioResult(success=True, audio_data=data, sample_rate=32000,
                                       format="wav", engine=self.name)
        except Exception as e:
            return AudioResult(success=False, error=str(e), engine=self.name)


register_engine(GPTSoVITS_v2proEngine)
