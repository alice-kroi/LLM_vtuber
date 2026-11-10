# ============================================================
# persona/learning/episode.py — 情景记忆的召回打分
# ============================================================
# 长期记忆不是"把聊天记录全塞进 prompt"，而是按两个因子挑最值得回忆的片段：
#   score = importance × exp(-0.02 × days) + 关键词命中加成
#   - exp(-0.02×days)：半衰期约 35 天（与设计文档 §8 的 weight 一致）
#   - importance：写入时由情绪强度决定的 0~1
#   - 关键词加成：当前这句话与摘要的 2-gram 重合，最多 +0.45
#
# 只召回当前 user 的条目；低于 MIN_SCORE 的不进 prompt。
# ============================================================

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DECAY_PER_DAY = 0.02
MIN_SCORE = 0.30
DEFAULT_LIMIT = 3
KEYWORD_BONUS = 0.15
KEYWORD_BONUS_MAX = 0.45
MAX_SUMMARY_CHARS = 60


def _utcnow() -> datetime:
    """朴素 UTC（与 SQLite CURRENT_TIMESTAMP 对齐，且不触发 utcnow 弃用告警）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def age_days(created_at: Any, now: Optional[datetime] = None) -> float:
    """created_at 是 SQLite 的 CURRENT_TIMESTAMP（UTC），所以用 UTC 比较"""
    if not created_at:
        return 0.0
    if isinstance(created_at, str):
        try:
            dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return 0.0
    elif isinstance(created_at, datetime):
        dt = created_at
    else:
        return 0.0
    now = now or _utcnow()
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def decay_weight(created_at: Any, now: Optional[datetime] = None) -> float:
    return math.exp(-DECAY_PER_DAY * age_days(created_at, now))


def _terms(text: str) -> set:
    body = re.sub(r"\s+", "", text or "")
    if len(body) < 2:
        return {body} if body else set()
    return {body[i:i + 2] for i in range(len(body) - 1)}


def score(row: Dict[str, Any], query: Optional[str] = None,
          now: Optional[datetime] = None) -> float:
    """单条情景的召回分数"""
    importance = float(row.get("importance") or 0.5)
    value = importance * decay_weight(row.get("created_at"), now)
    if query:
        summary = row.get("summary") or ""
        hits = sum(1 for t in _terms(query) if t in summary)
        value += min(KEYWORD_BONUS_MAX, KEYWORD_BONUS * hits)
    return round(value, 3)


def select(rows: List[Dict[str, Any]], query: Optional[str] = None,
           limit: int = DEFAULT_LIMIT,
           min_score: float = MIN_SCORE) -> List[Dict[str, Any]]:
    """按分数排序、去重、截断；返回带上 score 的行"""
    scored = []
    for row in rows or []:
        s = score(row, query)
        if s < min_score:
            continue
        item = dict(row)
        item["score"] = s
        scored.append(item)
    scored.sort(key=lambda r: (-r["score"], r.get("created_at") or ""))

    out: List[Dict[str, Any]] = []
    seen = set()
    for item in scored:
        key = re.sub(r"\s+", "", (item.get("summary") or ""))[:40]
        if key and key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _relative_day(created_at: Any) -> str:
    days = age_days(created_at)
    if days < 1:
        return "今天"
    if days < 2:
        return "昨天"
    return f"{int(days)} 天前"


def describe(row: Dict[str, Any]) -> str:
    """压成一行人话，供 prompt 与 WebUI 使用"""
    summary = re.sub(r"\s+", " ", (row.get("summary") or "")).strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "…"
    emotion = row.get("emotion")
    tail = f"（当时我感到 {emotion}）" if emotion else ""
    return f"{_relative_day(row.get('created_at'))}：{summary}{tail}"


__all__ = ["age_days", "decay_weight", "score", "select", "describe"]
