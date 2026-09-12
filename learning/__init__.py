#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
learning 模块 —— 自主学习 + 奖励机制

公开接口：
    RewardTracker         —— 奖励事件采集 + 量化 + 存储
    get_reward_tracker()  —— 获取全局实例
    init_reward_tracker() —— 初始化（绑定 MemoryManager）

    FeedbackInjector          —— 高奖励案例 few-shot 注入
    get_feedback_injector()   —— 获取全局实例

    KnowledgeCurator       —— 知识提炼 + 淘汰
    get_knowledge_curator() —— 获取全局实例

    SentimentScorer        —— 弹幕情感打分（工具类）
"""

from .sentiment_scorer import (
    score_danmu,
    batch_score,
    DanmuDensityTracker,
)
from .reward_tracker import (
    RewardEvent,
    ResponseRewardAggregator,
    RewardTracker,
    get_reward_tracker,
    init_reward_tracker,
)
from .feedback_injector import (
    FeedbackInjector,
    get_feedback_injector,
)
from .emotion_pacer import (
    EmotionPacer,
    get_emotion_pacer,
    reset_emotion_pacer,
)
from .reflection import (
    MemoryReflection,
    get_memory_reflection,
)
from .knowledge_curator import (
    KnowledgeCurator,
    get_knowledge_curator,
)

__all__ = [
    # sentiment_scorer
    "score_danmu",
    "batch_score",
    "DanmuDensityTracker",
    # reward_tracker
    "RewardEvent",
    "ResponseRewardAggregator",
    "RewardTracker",
    "get_reward_tracker",
    "init_reward_tracker",
    # feedback_injector
    "FeedbackInjector",
    "get_feedback_injector",
    # emotion_pacer
    "EmotionPacer",
    "get_emotion_pacer",
    "reset_emotion_pacer",
    # reflection
    "MemoryReflection",
    "get_memory_reflection",
    # knowledge_curator
    "KnowledgeCurator",
    "get_knowledge_curator",
]
