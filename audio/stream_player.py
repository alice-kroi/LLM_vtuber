#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真流式音频播放器：边收边播（IO 流式）

与 audio_deal.py 的 play_audio_stream_chunks（先收集全部再播）不同，
本模块用 sounddevice 的 OutputStream，每收到一个 PCM 块立即写入声卡播放。

用法：
    from audio.stream_player import play_pcm_stream
    await play_pcm_stream(chunk_iter, sample_rate=32000)

其中 chunk_iter 为异步迭代器，yield AudioChunk（见 tts_engine.py）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False
    np = None
    sd = None


def strip_wav_header(chunk: bytes) -> bytes:
    """若块以 RIFF 头开头，剥离到 data 段，返回纯 PCM。"""
    if chunk[:4] == b'RIFF':
        data_start = 12
        while data_start < len(chunk) - 8:
            cid = chunk[data_start:data_start + 4]
            csize = int.from_bytes(chunk[data_start + 4:data_start + 8], 'little')
            if cid == b'data':
                data_start += 8
                return chunk[data_start:]
            data_start += 8 + csize
        return chunk[44:] if len(chunk) > 44 else b""
    return chunk


async def play_pcm_stream(
    chunk_iter: AsyncIterator,
    sample_rate: int = 32000,
    output_device: Optional[int] = None,
    chunk_timeout: float = 5.0,
) -> dict:
    """
    边收边播 PCM 流。

    Args:
        chunk_iter: 异步迭代器，yield 含 .data(PCM int16 bytes) / .sample_rate 的对象
                    （AudioChunk 或 dict 均可），也兼容裸 bytes。
        sample_rate: 默认采样率（AudioChunk 自带时优先）
        output_device: 输出设备编号，None=默认
        chunk_timeout: 等待下一个块的超时（秒），超时视为结束

    Returns:
        {"success": bool, "error": str|None, "duration": float, "chunks": int}
    """
    result = {"success": False, "error": None, "duration": 0.0, "chunks": 0}
    if not SD_AVAILABLE:
        result["error"] = "sounddevice/numpy 未安装，无法流式播放"
        return result

    rate = sample_rate
    device = output_device if output_device is not None else sd.default.device[1]
    stream = None
    try:
        # 探测首块：拿到真实采样率；若首块是纯 WAV 头（剥离后空）则跳到下一块
        first = await asyncio.wait_for(chunk_iter.__anext__(), timeout=chunk_timeout)
        rate = getattr(first, "sample_rate", 0) or sample_rate
        data0 = first.data if hasattr(first, "data") else first
        pcm0 = strip_wav_header(data0)
        while not pcm0:
            # 纯头块：继续取下一块
            first = await asyncio.wait_for(chunk_iter.__anext__(), timeout=chunk_timeout)
            rate = getattr(first, "sample_rate", 0) or rate
            data0 = first.data if hasattr(first, "data") else first
            pcm0 = strip_wav_header(data0)

        stream = sd.OutputStream(samplerate=rate, channels=1,
                                 dtype="float32", device=device)
        stream.start()

        def _to_float(x: bytes) -> Optional[np.ndarray]:
            arr = np.frombuffer(x, dtype=np.int16)
            if arr.size == 0:
                return None
            return arr.astype(np.float32) / 32768.0

        # 首块即播（真流式起点）
        s0 = _to_float(pcm0)
        if s0 is not None:
            stream.write(s0)
            result["duration"] += len(s0) / rate
            result["chunks"] += 1

        # 逐块边收边播
        while True:
            try:
                chunk = await asyncio.wait_for(
                    chunk_iter.__anext__(), timeout=chunk_timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.warning(f"[stream_player] 等待音频块超时({chunk_timeout}s)，结束播放")
                break
            data = chunk.data if hasattr(chunk, "data") else chunk
            if hasattr(chunk, "sample_rate") and getattr(chunk, "sample_rate", 0):
                if chunk.sample_rate != rate:
                    rate = chunk.sample_rate  # 采样率切换（一般不会）
            pcm = strip_wav_header(data)
            if not pcm:
                continue
            s = _to_float(pcm)
            if s is None:
                continue
            stream.write(s)
            result["duration"] += len(s) / rate
            result["chunks"] += 1

        stream.stop()
        result["success"] = True
    except asyncio.TimeoutError:
        result["error"] = "等待首个音频块超时"
    except Exception as e:
        result["error"] = f"流式播放异常: {e}"
        logger.error(result["error"], exc_info=True)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
    return result
