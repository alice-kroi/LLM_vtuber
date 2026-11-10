#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可插拔 TTS 引擎层（借鉴 Cortico World 扩展包模式）

- TTSEngine：统一接口，任意引擎（本地 v2pro / 豆包云 / edge-tts / ...）实现即注册。
- 可拆卸：每个引擎一个模块放 audio/engines/，各自独立 import，不装就不注册。
- 核心契约：`astream()` 产出 AudioChunk（PCM bytes + rate），供"边收边播"真流式播放。
  （不是先获取整个音频再播放。）
- config.ini [tts] engine = auto | sovits_v2pro | doubao | edge_tts 驱动选择。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Type

logger = logging.getLogger(__name__)


@dataclass
class AudioChunk:
    """流式音频块：PCM int16 单声道 bytes + 采样率。"""
    data: bytes                 # PCM (int16, mono) 或封装好的可播字节
    sample_rate: int = 32000
    is_wav_header: bool = False  # 首块为 WAV 头时置 True（需跳过/剥离）
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioResult:
    """非流式（一次性）结果，供 synthesize() 使用。"""
    success: bool = False
    error: Optional[str] = None
    audio_data: bytes = b""
    sample_rate: int = 32000
    format: str = "wav"
    duration: float = 0.0
    engine: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class TTSEngine:
    """TTS 引擎接口。子类实现全部方法并注册。"""

    name: str = "base"                     # 唯一引擎名，对应 config.ini [tts] engine 值
    display_name: str = "Base TTS"

    # ---------- 能力探测 ----------

    def available(self) -> bool:
        """配置+依赖是否就绪（如本地服务端口是否可连 / 依赖包是否安装）。"""
        raise NotImplementedError

    # ---------- 流式（真 IO 流，首选）----------

    async def astream(self, text: str, *, tone: str = "",
                      ref_audio: str = "", **kw) -> AsyncIterator[AudioChunk]:
        """边合成边产出音频块。默认回退到 synthesize() 一次性产出。"""
        res = await self.synthesize(text, tone=tone, ref_audio=ref_audio, **kw)
        if not res.success or not res.audio_data:
            return
        yield AudioChunk(data=res.audio_data, sample_rate=res.sample_rate,
                         is_wav_header=True, meta={"engine": self.name})

    # ---------- 非流式（兜底/兼容）----------

    async def synthesize(self, text: str, *, tone: str = "",
                         ref_audio: str = "", **kw) -> AudioResult:
        """一次性合成完整音频。"""
        raise NotImplementedError

    # ---------- 描述 ----------

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name,
                "available": self.available()}


# ---------- 注册表 ----------

_ENGINES: Dict[str, Type[TTSEngine]] = {}


def register_engine(cls: Type[TTSEngine]) -> Type[TTSEngine]:
    """装饰器/直接调用：注册引擎类。"""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"引擎类 {cls.__name__} 缺少 name 属性")
    _ENGINES[name] = cls
    logger.info(f"[TTSEngine] 已注册引擎: {name} ({cls.__name__})")
    return cls


def get_engine_names() -> list:
    return list(_ENGINES.keys())


def get_engine(name: str) -> Optional[TTSEngine]:
    cls = _ENGINES.get(name)
    return cls() if cls else None


def resolve_engine(preferred: str = "auto") -> Optional[TTSEngine]:
    """按偏好选择可用引擎；auto 时按优先级：sovits_v2pro > doubao > edge_tts。"""
    if preferred and preferred != "auto":
        eng = get_engine(preferred)
        if eng and eng.available():
            return eng
        logger.warning(f"[TTSEngine] 引擎 '{preferred}' 不可用，回退自动选择")
    for name in ("sovits_v2pro", "doubao", "edge_tts"):
        eng = get_engine(name)
        if eng and eng.available():
            return eng
    return None
