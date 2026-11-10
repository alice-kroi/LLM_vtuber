#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0-1 弹幕降噪与限流（借鉴 Cortico B站 World 的投递过滤层）

背景：B站高频弹幕容易导致上下文预算膨胀、LLM 被无关消息刷屏。
做法：
- 重要事件（礼物/SC/上舰）识别并高优先级放行（不参与限流，不被丢弃）。
- 普通弹幕按「时间窗口 + 条数上限」限流；超限的普通弹幕做"归档式丢弃"（记日志，
  不再喂给 LLM），保证"什么值得喂给 LLM"是一个独立的投递过滤层。

与 main.handle_post_request 的既有去重协同：先内容去重，再做本窗口限流。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class DanmuFilter:
    """普通弹幕限流 + 重要事件识别。线程/协程安全（仅新增计时字段，无共享写竞态）。"""

    def __init__(self, window_s: float = 0.3, max_per_window: int = 64):
        self.window_s = window_s          # 时间窗口(秒)：借鉴 Cortico ~300ms 投递归并窗口
        self.max_per_window = max_per_window  # 窗口内普通弹幕允许条数上限（Cortico 64 条容量）
        self._win_start = 0.0             # 当前窗口起始时间
        self._win_count = 0               # 当前窗口已放行普通弹幕数
        self._dropped_total = 0           # 累计丢弃普通弹幕数（归档式）
        self._important_total = 0         # 累计放行重要事件数

    # ---------- 重要事件识别 ----------

    def is_important(self, msg: Any) -> bool:
        """重要事件判定：礼物 / SC / 上舰（监听端已标 important，或内容含关键词）。"""
        if not isinstance(msg, dict):
            return False
        if msg.get("important"):
            return True
        content = str(msg.get("content") or "")
        # 兼容老监听端：礼物/SC 在转发时被拼进了 content
        return ("赠送了" in content) or ("SC]" in content) or ("超级留言" in content) or ("上舰" in content)

    # ---------- 分派判定 ----------

    def should_deliver(self, msg: Any) -> Tuple[bool, str]:
        """决定一条消息是否投递进 LLM 队列。

        Returns:
            (deliver, reason): deliver=True 进入队列；否则应被归档丢弃。
            reason 取值：important / ok / rate_dropped
        """
        if self.is_important(msg):
            # 重要事件即时放行，并复位窗口（不被后续普通弹幕限流牵连）
            self._advance_window_if_needed()
            self._important_total += 1
            return True, "important"

        self._advance_window_if_needed()
        if self._win_count < self.max_per_window:
            self._win_count += 1
            return True, "ok"

        self._dropped_total += 1
        # 普通弹幕超限 → 归档式丢弃（避免刷屏喂给 LLM）
        logger.debug(
            f"[去噪限流] 窗口[{self.window_s:.2f}s]普通弹幕已达{self.max_per_window}条，"
            f"归档丢弃(累计{self._dropped_total}) content={str(msg.get('content', ''))[:30]}"
        )
        return False, "rate_dropped"

    def mark_important(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """为重要事件打标（供下游奖励/历史复用）。不改变原字典。"""
        return {**msg, "important": True}

    # ---------- 内部 ----------

    def _advance_window_if_needed(self) -> None:
        now = time.time()
        if now - self._win_start > self.window_s:
            self._win_start = now
            self._win_count = 0

    def stats(self) -> Dict[str, Any]:
        return {
            "window_s": self.window_s,
            "max_per_window": self.max_per_window,
            "dropped": self._dropped_total,
            "important": self._important_total,
        }


# 单例：main 与监听端共用同一实例，保证限流窗口一致
danmu_filter = DanmuFilter()