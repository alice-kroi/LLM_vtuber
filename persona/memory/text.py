# ============================================================
# persona/memory/text.py — 记忆文本工具
# ============================================================
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional

_CN_STOP = {
    "工具", "能力", "用于", "可以", "一个", "这个", "那个", "进行", "通过",
    "获取", "查询", "计算", "转换", "支持", "输入", "输出", "返回", "结果",
    "用户", "需要", "如果", "以及", "相关", "数据", "信息", "内容", "操作",
    "方法", "函数", "使用", "时候", "什么", "怎么", "是否", "并且", "或者",
    "然后", "现在", "已经", "没有", "我们", "你们", "他们", "任务", "功能",
    "记忆", "记得", "告诉", "一下", "知道", "帮我", "你的", "我的",
}
_EN_STOP = {
    "the", "and", "for", "with", "tool", "function", "returns", "return",
    "input", "output", "user", "using", "used", "this", "that", "from",
    "into", "can", "will", "should", "value", "values", "result", "results",
}


def tokens(text: str) -> set:
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", raw)) - _EN_STOP
    grams: set = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", raw):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in _CN_STOP:
                grams.add(gram)
    return words | grams


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    shared = ta & tb
    containment = len(shared) / max(1, min(len(ta), len(tb)))
    jaccard = len(shared) / max(1, len(ta | tb))
    return round(max(jaccard, containment * 0.7 + jaccard * 0.3), 4)


def phrase_containment(a: str, b: str) -> float:
    """较短一方的语义片段被较长一方覆盖的比例（用于近重复合并）。"""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    shared = ta & tb
    return round(len(shared) / max(1, min(len(ta), len(tb))), 4)


def recency_score(created_at: str, half_life_days: float = 30.0) -> float:
    try:
        ts = time.mktime(time.strptime(created_at[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return 0.5
    age_days = max(0.0, (time.time() - ts) / 86400.0)
    return math.exp(-age_days / max(1.0, half_life_days))


def clip(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


def visibility_for(source: str, chat_key: str = "") -> str:
    source = str(source or "").lower()
    if source == "qq":
        if str(chat_key).startswith("qq:group:"):
            return "group"
        return "private"
    if source in ("bilibili", "bili_live"):
        return "public"
    return "private"


def session_key(source: str, user: str, chat_key: str = "") -> str:
    return str(chat_key or f"{source}:{user or 'anonymous'}")[:160]


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "tokens", "similarity", "phrase_containment", "recency_score", "clip",
    "visibility_for", "session_key", "now_str",
]
