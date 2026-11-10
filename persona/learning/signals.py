# ============================================================
# persona/learning/signals.py — 情绪 → 奖励换算
# ============================================================
# 不引入奖励模型：Self.perceive() 产出的 emotion + intensity 本身就是
# 带解释的即时奖励，这里只做符号与量纲换算。
#
# r_event = polarity(emotion) × intensity × (0.5 + affinity)
#   affinity 0.0 → ×0.5；affinity 1.0 → ×1.5（熟人反馈更有分量）
#   anger 若责任在对方（blame_other）记 0，避免"记恨用户"
# ============================================================

from __future__ import annotations

from typing import Optional

POSITIVE_EMOTIONS = {
    "joy", "gratitude", "satisfaction", "relief", "pride", "hope", "interest",
}
NEGATIVE_EMOTIONS = {
    "sadness", "distress", "disappointment", "fear", "shame", "boredom",
}


def polarity(emotion: Optional[str]) -> int:
    """情绪方向：+1 有利 / -1 不利 / 0 中性或未知"""
    if not emotion:
        return 0
    if emotion in POSITIVE_EMOTIONS:
        return 1
    if emotion in NEGATIVE_EMOTIONS or emotion == "anger":
        return -1
    return 0


def relation_weight(affinity: float) -> float:
    """关系权重：0.5 ~ 1.5"""
    return 0.5 + max(0.0, min(1.0, affinity))


def to_reward(emotion: Optional[str], intensity: Optional[float],
              affinity: float = 0.2, blame_other: bool = False) -> float:
    """单次事件的奖励，范围约 [-1.5, 1.5]"""
    pol = polarity(emotion)
    if emotion == "anger" and blame_other:
        pol = 0
    if pol == 0 or intensity is None:
        return 0.0
    return round(pol * max(0.0, min(1.0, float(intensity))) * relation_weight(affinity), 3)


__all__ = ["POSITIVE_EMOTIONS", "NEGATIVE_EMOTIONS", "polarity",
           "relation_weight", "to_reward"]
