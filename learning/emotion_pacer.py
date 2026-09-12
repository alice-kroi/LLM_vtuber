#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪节奏器（Emotion Pacer）

对应方案「思考方式升级 B4：情绪与节奏感知」。解决 AI 主播两大问题：
1. **审美疲劳**：同一语气（如"开心"）高频出现 → 自动建议换语气
2. **节奏呆板**：不进不影响情绪；弹幕高潮时不知道更活跃、感谢

设计原则：
- 纯逻辑、无外部依赖（不调 LLM），可独立单元测试
- 只输出「建议」，由调用方（main.py）选择性地改写 system_prompt
- 有滑窗统计，天然对短时波动免疫
"""

import logging
import time
from collections import deque, Counter
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class EmotionPacer:
    """
    情绪节奏器

    维护一个语气滑动窗口，结合实时奖励信号，
    产出「语气多样化 + 情绪活跃度」建议。

    建议的三种形态：
    - variability：近期语气过于单一，建议换一个
    - energy_up   ：收到强正反馈，建议更活泼/致谢
    - energy_down ：连续低反馈，建议降低热情、自然转移话题
    - neutral     ：无需调整
    """

    def __init__(
        self,
        window_size: int = 20,
        dominance_ratio: float = 0.5,
        repeat_threshold: int = 6,
        high_reward_threshold: int = 10,
        low_reward_threshold: int = -3,
    ):
        """
        Args:
            window_size: 语气滑窗大小
            dominance_ratio: 某语气占比超过此值触发「换语气」
            repeat_threshold: 同一语气连续出现次数超过此值触发「强制换」
            high_reward_threshold: 奖励分高于此值视为强正反馈
            low_reward_threshold: 奖励分低于此值视为低反馈
        """
        self.window_size = window_size
        self.dominance_ratio = dominance_ratio
        self.repeat_threshold = repeat_threshold
        self.high_reward_threshold = high_reward_threshold
        self.low_reward_threshold = low_reward_threshold

        self._tones: deque[str] = deque(maxlen=window_size)
        self._last_tone: Optional[str] = None
        self._consecutive_tone = 0

    # ============ 记录 ============

    def record_tone(self, tone: str):
        """记录本次回复使用的情感（语气）"""
        tone = (tone or "普通").strip()
        if not tone:
            tone = "普通"

        if tone == self._last_tone:
            self._consecutive_tone += 1
        else:
            self._consecutive_tone = 1
            self._last_tone = tone

        self._tones.append(tone)

    # ============ 统计 ============

    def get_tone_distribution(self) -> List[Dict[str, Any]]:
        """获取滑窗内语气分布（按频率降序）"""
        if not self._tones:
            return []
        counter = Counter(self._tones)
        total = sum(counter.values())
        return [
            {"tone": t, "count": c, "ratio": round(c / total, 3)}
            for t, c in counter.most_common()
        ]

    def get_dominant_tone(self) -> Optional[str]:
        """返回滑窗内占比超过 dominance_ratio 的单一语气"""
        dist = self.get_tone_distribution()
        if not dist:
            return None
        top = dist[0]
        if top["ratio"] >= self.dominance_ratio:
            return top["tone"]
        return None

    @property
    def recent_tone(self) -> Optional[str]:
        """最近一次使用的语气"""
        return self._last_tone

    # ============ 建议生成 ============

    def get_advice(self, recent_reward_score: float = 0.0) -> Dict[str, Any]:
        """
        基于语气分布 + 奖励信号生成节奏建议

        Args:
            recent_reward_score: 最近一次回复聚合的奖励分（0 表示未知/中性）

        Returns:
            {
                "kind": "neutral"|"variability"|"energy_up"|"energy_down",
                "reason": str,
                "suggestion": str,     # 人话建议
                "detail": {...}        # 结构化数据
            }
        """
        # 1. 强制换语气：同一语气连续出现太多次
        if self._consecutive_tone >= self.repeat_threshold:
            return {
                "kind": "variability",
                "reason": f"连续 {self._consecutive_tone} 次使用「{self._last_tone}」语气，容易让观众审美疲劳",
                "suggestion": f"这回合换一个与「{self._last_tone}」不同的语气，比如试试正经、疑问或俏皮一点。",
                "detail": {"tone": self._last_tone, "consecutive": self._consecutive_tone},
            }

        # 2. 占比过高 → 换语气
        dominant = self.get_dominant_tone()
        if dominant:
            return {
                "kind": "variability",
                "reason": f"近期「{dominant}」语气占比过高",
                "suggestion": f"换个与「{dominant}」不同的语气说话，让回复更有变化。",
                "detail": {"tone": dominant, "distribution": self.get_tone_distribution()},
            }

        # 3. 强正反馈 → 更活跃/致谢
        if recent_reward_score >= self.high_reward_threshold:
            return {
                "kind": "energy_up",
                "reason": f"收到强正反馈（奖励 +{recent_reward_score:.0f}）",
                "suggestion": "观众很热情，这回合语气可以更活泼、更积极，适当感谢观众的互动。",
                "detail": {"reward_score": recent_reward_score},
            }

        # 4. 低反馈 → 降低热情、自然转移
        if recent_reward_score <= self.low_reward_threshold:
            return {
                "kind": "energy_down",
                "reason": f"近期反馈较低（奖励 {recent_reward_score:.0f}）",
                "suggestion": "这回合语气放轻松些，不必过于亢奋，自然一点，也可以自然地换个话题。",
                "detail": {"reward_score": recent_reward_score},
            }

        return {
            "kind": "neutral",
            "reason": "节奏正常",
            "suggestion": "",
            "detail": {"distribution": self.get_tone_distribution()},
        }

    # ============ 改写 system_prompt ============

    def nudge_system_prompt(
        self, system_prompt: str, advice: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        把建议追加到 system_prompt 尾部

        未启用/中性建议/无建议时原样返回 system_prompt。
        """
        if advice is None:
            advice = self.get_advice()

        if advice.get("kind") == "neutral" or not advice.get("suggestion"):
            return system_prompt

        nudge = (
            f"\n\n## 本轮节奏提示\n{advice['suggestion']}"
            "\n（优先遵守此提示把握语气，但不要生硬照搬。）"
        )
        return f"{system_prompt}{nudge}"


# ============ 全局单例 ============

_instance: Optional[EmotionPacer] = None


def get_emotion_pacer(**kwargs) -> EmotionPacer:
    """获取全局 EmotionPacer 实例（首次调用时创建）"""
    global _instance
    if _instance is None:
        _instance = EmotionPacer(**kwargs)
    return _instance


def reset_emotion_pacer(**kwargs):
    """重置单例（测试用）"""
    global _instance
    _instance = None
    return get_emotion_pacer(**kwargs)