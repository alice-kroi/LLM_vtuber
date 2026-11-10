#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 引擎包：导入即注册（可拆卸——不装某引擎模块，它就不注册）。

导入本包会触发所有 engines/*.py 的 register_engine()。
若某个引擎依赖缺失（edge_tts 未安装等），其模块内部 try/except 已兜底，不会阻断其他引擎。
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_ENGINE_MODULES = ("sovits_v2pro", "doubao", "edge_tts")


def load_all() -> None:
    """尝试加载全部引擎模块（缺依赖的引擎模块自身会兜底，不影响其他）。

    用相对导入（from . import X），保证 enginers 内引擎与外部共用
    顶层 tts_engine 注册表（引擎模块内都是 from tts_engine import ...）。
    """
    for mod in _ENGINE_MODULES:
        try:
            importlib.import_module(f".{mod}", __name__)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[TTSEngine] 引擎模块 {mod} 加载失败(将不可用): {e}")


load_all()
