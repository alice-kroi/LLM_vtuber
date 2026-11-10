# ============================================================
# LLM_v2/domains/bili_live/bili_live_listener.py — B 站直播间监听器
# ============================================================
# 注：复用 V1 LLM/bili/bili_listener.py 的实现（行为一致），
# 仅作为 bili_live 子进程引擎的载体。
# ============================================================

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from LLM.bili.bili_listener import BiliListener as _V1BiliListener

    class BiliListener(_V1BiliListener):
        """V2 BiliListener：复用 V1 实现（行为一致）"""
        pass
except ImportError as e:
    logger.warning(f"[bili_live] V1 BiliListener 导入失败，提供 stub: {e}")

    class BiliListener:  # type: ignore[no-redef]
        """BiliListener 占位（V1 不可用时）"""

        def __init__(self):
            self._started = False

        def start(self, room_ids=None):
            self._started = True
            return {"success": False, "error": "BiliListener 未配置（V1 模块缺失）"}

        def stop(self):
            self._started = False
            return {"success": True}

        def status(self):
            return {"running": False, "rooms": [], "buffer_size": 0}

        def observe(self, limit: int = 20):
            return []
