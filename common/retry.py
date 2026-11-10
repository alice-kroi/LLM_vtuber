#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0-3 结构化重试策略（借鉴 Cortico ResubmitPolicy）。

背景：LLM/TTS 关键调用失败只打裸日志，无法区分"可重试(0/429/5xx)"与"不可重试(401/输入超限)"。
做法：
- RetryPolicy：max_consecutive=2 / max_per_batch=4 / backoff_ms=[2000,10000]。
- classify：按异常/状态码分类 retryable / non_retryable。
- run_with_retry：异步包装，按策略退避重试，结构化日志（sess/round/tool/status/attempt）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 可重试状态码：网络层(0) / 限流(429) / 服务端(5xx)
_RETRYABLE_STATUS = {0, 429, 500, 502, 503, 504}


def is_retryable_status(status: Optional[int]) -> bool:
    """非真状态码(0/None)按可重试处理；429/5xx 可重试；其它 4xx(如 401认证)不可重试。"""
    if status is None:
        return True
    return status in _RETRYABLE_STATUS or status >= 500


@dataclass
class RetryPolicy:
    """同批次内失败后的重试策略（与 Cortico DEFAULT_RESUBMIT 对齐）。"""
    max_consecutive: int = 2          # 允许的连续失败次数（第三次连续失败即放弃）
    max_per_batch: int = 4            # 一批内总重试次数上限
    backoff_ms: list = field(default_factory=lambda: [2000, 10000])  # 第 n 次失败后的等待


DEFAULT_RETRY_POLICY = RetryPolicy()


class RetryableError(Exception):
    """可重试错误的载体：传入不透明状态码供 classify 使用。"""

    def __init__(self, message: str, status: Optional[int] = None, **context):
        super().__init__(message)
        self.status = status
        self.context = context


def classify(exc: BaseException, default_status: Optional[int] = None) -> tuple:
    """把异常分类为 (retryable, status)。

    - 显式 RetryableError → 按 status 判断
    - 超时类 (asyncio.TimeoutError / OSError timeout) → 可重试
    - 其余 → 不可重试
    """
    retryable_excs = (asyncio.TimeoutError, TimeoutError)
    if isinstance(exc, retryable_excs):
        return True, 0
    if isinstance(exc, RetryableError):
        status = exc.status if exc.status is not None else default_status
        return status in _RETRYABLE_STATUS, status
    # 其他未知异常：不盲目重试，交由上层核对
    return False, default_status


async def _sleep(ms: int, cancel_event: Optional[asyncio.Event] = None) -> bool:
    """等待 backoff；返回 False 表示被取消。"""
    if ms <= 0:
        return True
    if cancel_event is None:
        await asyncio.sleep(ms / 1000.0)
        return True
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=ms / 1000.0)
    except asyncio.TimeoutError:
        return True
    return False  # 被取消


async def run_with_retry(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    task_id: str = "",
    ctx: Optional[Dict[str, Any]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    default_status: Optional[int] = None,
) -> Dict[str, Any]:
    """按 RetryPolicy 执行一个可重试协程。

    Args:
        coro_factory: 每次重试都重新创建协程（如新的 HTTP 请求）
        policy: 重试策略
        task_id / ctx: 结构化日志关联字段
        cancel_event: 协作式取消（backoff 等待期间可立即中止）
        default_status: 未知异常时假定的状态码

    Returns:
        {"ok": bool, "data": 成功结果或None, "attempts": 尝试次数,
         "retried": bool, "status": 最终状态码, "error": 错误信息}
    """
    ctx = ctx or {}
    attempts = 0
    consecutive = 0
    status = default_status
    last_error: Optional[BaseException] = None
    while True:
        attempts += 1
        start = time.time()
        status = default_status
        try:
            result = await coro_factory()
            if attempts > 1:
                logger.info(f"[retry] 恢复成功 (attempt={attempts}, total_elapsed_ms={int((time.time()-start)*1000)}) "
                            f"task={task_id or ctx.get('sess', '')}", )
            return {"ok": True, "data": result, "attempts": attempts,
                    "retried": attempts > 1, "status": status, "error": None}
        except Exception as e:  # noqa: BLE001
            last_error = e
            retryable, status = classify(e, default_status)
            consecutive = consecutive + 1 if retryable else consecutive
            logger.warning(
                f"[retry] 调用失败 (attempt={attempts}, retryable={retryable}, status={status}, "
                f"consecutive={consecutive}, task={task_id or ctx.get('sess', '')}, "
                f"ctx={json_dumps(ctx)}) err={str(e)[:120]}"
            )
            if (not retryable
                    or consecutive > policy.max_consecutive
                    or attempts - 1 >= policy.max_per_batch):
                return {"ok": False, "data": None, "attempts": attempts,
                        "retried": attempts > 1, "status": status, "error": str(e)}
            delay_ms = policy.backoff_ms[min(consecutive, len(policy.backoff_ms)) - 1]
            if not await _sleep(delay_ms, cancel_event):
                return {"ok": False, "data": None, "attempts": attempts,
                        "retried": attempts > 1, "status": status, "error": "cancelled"}


def json_dumps(obj: Any) -> str:
    try:
        import json
        return json.dumps(obj, ensure_ascii=False, default=str)[:200]
    except Exception:
        return str(obj)[:200]