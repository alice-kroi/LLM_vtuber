# ============================================================
# persona/emotion.py — 情绪来源（事件 → 认知评估 → OCC 情绪 → 强度）
# ============================================================
# 职责：把"发生了什么"翻译成一个可解释的情绪标签 + 强度。
#   输出交给 persona/self.py 的 Self.perceive() 去生成 PAD 脉冲。
#
# 为什么用规则而不是 LLM：
#   - 可复现：同一事件永远得到同一情绪，不会因为模型随机性让性格漂移
#   - 可解释：每条规则都写明 Scherer CPM 四类评估（相关性/后果/应对/规范）
#   - 零成本：不占 token、不增加延迟
#   文本层面的细粒度情感识别（讽刺、反话）留到后续接模型，不混进这一层。
#
# 情绪标签只取 emotion_pad.json 里 status=verified_primary 的 9 个：
#   joy / gratitude / satisfaction / pride / hope / fear / anger / shame / sadness
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

# 情绪强度的人格调制：神经质越高，同样的事件反应越强
NEUROTICISM_GAIN = 0.30


@dataclass(frozen=True)
class Appraisal:
    """一次认知评估的结果（四类检查全部显式记录，便于审计）"""
    emotion: str
    intensity: float          # 0-1，基础值
    relevance: str            # 相关性：这件事与我有关吗、重要吗
    implication: str          # 后果：对我有利/不利，是否超出预期
    coping: str               # 应对能力：我能不能处理
    normative: str            # 规范显著性：是否符合我的价值观、谁的责任


# 事件 → 评估结果。键与 LLM_v2/events.py 的事件类型一致。
_RULES: Dict[str, Appraisal] = {
    "gift_received": Appraisal(
        emotion="gratitude", intensity=0.75,
        relevance="高：观众直接为我付出",
        implication="有利，且超出日常预期",
        coping="无需应对，只需回应",
        normative="对方善意，符合我重视的诚实与取悦观众",
    ),
    "chat_received": Appraisal(
        emotion="joy", intensity=0.35,
        relevance="中：有人来找我说话",
        implication="有利，符合聊天预期",
        coping="轻松应对",
        normative="正常互动",
    ),
    "chat_replied": Appraisal(
        emotion="satisfaction", intensity=0.30,
        relevance="中：我刚完成一次回应",
        implication="有利，目标达成",
        coping="已处理完毕",
        normative="尽到主播职责",
    ),
    "task_done": Appraisal(
        emotion="pride", intensity=0.70,
        relevance="高：这是我亲手做的事",
        implication="有利，且是我努力的结果",
        coping="已成功完成",
        normative="符合完美主义与效率价值观",
    ),
    "task_failed": Appraisal(
        emotion="sadness", intensity=0.55,
        relevance="高：我的任务没做成",
        implication="不利，且低于预期",
        coping="暂时没能应对",
        normative="未能达到自己的标准",
    ),
    "will_engine_decided": Appraisal(
        emotion="hope", intensity=0.25,
        relevance="中：我刚做了一个决定",
        implication="不确定，但倾向于有利",
        coping="准备去执行",
        normative="符合内在驱动",
    ),
    # 以下事件刻意不给情绪：只影响资源条，不制造心情
    "needs_clarification": None,
    "rest_requested": None,
    "perception_updated": None,
    "idle_tick": None,
    "user_command": None,
}


def appraise(event_type: str, ctx: Optional[Dict[str, Any]] = None,
             neuroticism: float = 0.5) -> Optional[Appraisal]:
    """评估一个事件应该产生什么情绪。

    Args:
        event_type: 事件类型（EVENTS.*）
        ctx: 事件上下文；可用 ctx["intensity"] 显式覆盖强度
        neuroticism: 人格的神经质（0-1），用于调制强度

    Returns:
        Appraisal 或 None（该事件不产生情绪）
    """
    rule = _RULES.get(event_type)
    if rule is None:
        return None

    intensity = rule.intensity
    override = (ctx or {}).get("intensity")
    if isinstance(override, (int, float)):
        intensity = float(override)
    else:
        # 神经质调制：0.5 为中性，1.0 时放大 15%，0.0 时缩小 15%
        intensity *= 1 + NEUROTICISM_GAIN * ((neuroticism - 0.5) * 2) * 0.5

    intensity = max(0.0, min(1.0, intensity))
    if intensity < 0.05:
        return None
    return replace(rule, intensity=round(intensity, 3))


# ---------- 文本信号（确定性词表，不调模型）----------
# 只处理"明确夸奖/明确攻击"，不做讽刺与反话识别（那需要模型）。
# 命中多个词时取最强的一个，每多命中一个加 0.05，上限 0.9。
_POSITIVE_TERMS: Dict[str, float] = {
    "厉害": 0.60, "好棒": 0.65, "棒极了": 0.70, "喜欢": 0.55,
    "可爱": 0.55, "爱你": 0.80, "牛": 0.50, "666": 0.50,
    "awsl": 0.60, "优秀": 0.55, "漂亮": 0.55, "好听": 0.50,
}
# 道谢单独归 gratitude（OCC 里感激与喜悦是两种情绪）
_GRATITUDE_TERMS: Dict[str, float] = {
    "谢谢": 0.65, "感谢": 0.65, "辛苦了": 0.60, "多谢": 0.60,
}
_NEGATIVE_TERMS: Dict[str, float] = {
    "滚": 0.60, "垃圾": 0.60, "傻": 0.50, "蠢": 0.55,
    "去死": 0.80, "恶心": 0.65, "丑": 0.50, "闭嘴": 0.60,
    "讨厌": 0.50, "烦人": 0.50, "废物": 0.60,
}
_NEGATORS = ("不", "没", "别", "勿", "无")
_EMOJI_TERMS = {"😊": 0.45, "😄": 0.50, "❤": 0.60, "👍": 0.45, "😡": 0.55}


def appraise_text(text: str) -> Optional[Appraisal]:
    """从文本里读出明确情绪；读不出就返回 None（不硬猜）。"""
    if not text or not isinstance(text, str):
        return None
    body = text.strip()
    if not body:
        return None

    hits: Dict[str, float] = {}
    for table, kind in ((_GRATITUDE_TERMS, "gratitude"),
                        (_POSITIVE_TERMS, "joy"),
                        (_NEGATIVE_TERMS, "anger"),
                        (_EMOJI_TERMS, "joy")):
        for term, weight in table.items():
            idx = body.find(term)
            if idx < 0:
                continue
            if any(c in _NEGATORS for c in body[max(0, idx - 2):idx]):
                continue      # 被否定的夸奖不算夸奖
            hits[kind] = max(hits.get(kind, 0.0), weight)

    if not hits:
        return None

    kind = max(hits, key=lambda k: hits[k])
    intensity = min(0.90, hits[kind] + 0.05 * (len(hits) - 1))
    matched = kind == "anger" and "攻击性用语" or (
        kind == "gratitude" and "道谢" or "夸奖")
    return Appraisal(
        emotion=kind, intensity=round(intensity, 3),
        relevance=f"高：文本里有明确的{matched}",
        implication=("不利，且针对我" if kind == "anger"
                     else "有利，对方主动表达善意"),
        coping="需要回应" if kind == "gratitude" else "可忽略或回敬",
        normative=("不符合我的底线" if kind == "anger" else "符合我重视的互动"),
    )


def describe(ap: Optional[Appraisal]) -> str:
    """把评估结果压成一行，供日志与 WebUI 展示"""
    if ap is None:
        return "无情绪"
    return (f"{ap.emotion}({ap.intensity:.2f})｜"
            f"相关性:{ap.relevance}｜后果:{ap.implication}")


__all__ = ["Appraisal", "appraise", "appraise_text", "describe"]
