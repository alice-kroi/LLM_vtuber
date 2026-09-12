#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
奖励追踪模块

从弹幕、礼物、SC等事件中采集奖励信号，量化打分并存储，
供 feedback_injector（prompt 注入）和 knowledge_curator（知识提炼）消费。

评分体系：
┌──────────────┬─────────────────────────────┬─────────────────────┐
│ 奖励类型      │ 信号来源                     │ 奖励分               │
├──────────────┼─────────────────────────────┼─────────────────────┤
│ 强奖励       │ 礼物（电池值）               │ gift_battery / 10    │
│ 强奖励       │ SC 醒目留言（金额）           │ 直接用金额（分）      │
│ 中奖励       │ 弹幕正面关键词                │ +3 / 条              │
│ 弱惩罚       │ 弹幕负面关键词                │ -2 / 条              │
│ 隐式正面     │ 回复后弹幕密度突增            │ +1                   │
│ 手动评分     │ WebUI 运营者打分             │ ±5（可配置）          │
└──────────────┴─────────────────────────────┴─────────────────────┘

数据流向：
  bili_main → handle_post_request → RewardTracker.on_message() → MemoryManager.reward_store
                                                                       ↓
                                                      feedback_injector / knowledge_curator
"""

import asyncio
import logging
import time
from typing import List, Dict, Any, Optional

from .sentiment_scorer import score_danmu, DanmuDensityTracker

logger = logging.getLogger(__name__)


# ============ 礼物电池值参考表（粗略，按礼物等级估算） ============
# 礼物实际电池值需要从 B 站 API 获取，但粗略估算可以先用这个
# 详细列表见 https://github.com/ddiu8081/ bili-gift
_GIFT_BATTERY_ESTIMATE: Dict[str, int] = {
    "小心心": 0,       # 免费礼物
    "棒棒糖": 5,
    "辣条": 10,
    "荧光棒": 10,
    "B坷垃": 19,
    "鸡蛋": 20,
    "冰可乐": 30,
    "甜莓莓": 50,
    "能量球": 100,
    "礼花筒": 124,
    "人气票": 188,
    "浪漫花火": 300,
    "蛋糕": 420,
    "摩天轮": 1314,
    "云霄飞车": 3456,
    "爱心抱枕": 5200,
    "嘉年华": 124500,
}

# 默认电池值（不在上表中的礼物）
_DEFAULT_GIFT_BATTERY = 10


class RewardEvent:
    """
    单条奖励事件数据类

    用 dict 表示而非 dataclass，方便通过 LangGraph store 序列化。
    """

    @staticmethod
    def create(
        score: int,
        source: str,
        content: str = "",
        username: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        创建一条奖励事件记录

        Args:
            score: 奖励分（可正可负）
            source: 奖励来源 —— "danmu_pos"/"danmu_neg"/"gift"/"sc"/"density_spike"/"manual"
            content: 触发内容（弹幕文本、礼物名等）
            username: 触发者用户名
            metadata: 附加数据（礼物数量、电池值等）
        """
        return {
            "type": "reward",
            "score": score,
            "source": source,
            "content": content,
            "username": username,
            "metadata": metadata or {},
            "timestamp": time.time(),
            # 关联字段（后续由 _bind_response 填充）
            "response_id": None,
            "response_text": None,
        }


class ResponseRewardAggregator:
    """
    单轮 AI 回复的奖励聚合器

    在 AI 回复生成后、弹幕陆续返回时，把这段时间内的所有奖励事件累计，
    计算出这条回复的"总奖励分"，然后一次性写入 reward_store。

    这样可以避免每条弹幕单独写一次 store，也能在 knowledge_curator 提炼时
    直接拿到"这条回复值不值得学习"的完整信号。
    """

    def __init__(self, response_text: str, response_timestamp: float):
        self.response_text = response_text
        self.response_timestamp = response_timestamp
        self.events: List[Dict[str, Any]] = []
        self.total_score = 0
        self._finalized = False

    def add_event(self, event: Dict[str, Any]):
        """添加一条奖励事件"""
        if self._finalized:
            return
        self.events.append(event)
        self.total_score += event.get("score", 0)

    def finalize(self) -> Dict[str, Any]:
        """
        结束采集，返回聚合结果

        在 AI 回复后 30s 调用（由 RewardTracker 定时器触发）。
        """
        self._finalized = True
        return {
            "type": "response_reward",
            "response_text": self.response_text,
            "response_timestamp": self.response_timestamp,
            "total_score": self.total_score,
            "event_count": len(self.events),
            "positive_count": sum(1 for e in self.events if e.get("score", 0) > 0),
            "negative_count": sum(1 for e in self.events if e.get("score", 0) < 0),
            "events": self.events,
            "is_high_reward": self.total_score >= 5,
            "is_low_reward": self.total_score <= -3,
            "timestamp": time.time(),
        }


class RewardTracker:
    """
    奖励追踪器（全局单例）

    职责：
    1. 接收来自 handle_post_request 的消息
    2. 识别奖励信号（弹幕情感、礼物、SC）
    3. 量化打分
    4. 将奖励事件关联到最近的 AI 回复
    5. 通过 MemoryManager 持久化
    """

    _instance: Optional["RewardTracker"] = None

    def __init__(self):
        self._initialized = False
        self._memory_manager = None
        self._density_tracker = DanmuDensityTracker(window_seconds=10.0)
        # 当前活跃的 ResponseRewardAggregator（只有一个 AI 回复在被追踪）
        self._current_aggregator: Optional[ResponseRewardAggregator] = None
        # 历史聚合结果（保留最近 100 条供快速查询）
        self._recent_aggregations: List[Dict[str, Any]] = []
        self._max_recent = 100
        # 统计计数器
        self._stats = {
            "total_events": 0,
            "danmu_pos": 0,
            "danmu_neg": 0,
            "gift": 0,
            "sc": 0,
            "manual": 0,
        }

    @classmethod
    def get_instance(cls) -> "RewardTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def initialize(self, memory_manager=None):
        """初始化（绑定 MemoryManager）"""
        if self._initialized:
            return
        self._memory_manager = memory_manager
        self._initialized = True
        logger.info("RewardTracker 初始化完成")

    # ============ 入口：消息采集 ============

    async def on_messages(self, messages: List[Dict[str, Any]]):
        """
        接收一批消息（来自 handle_post_request）

        Args:
            messages: OpenAI 格式的消息列表，额外包含:
                - type: "danmu" / "gift" / "sc"
                - name: 发送者
                - content: 弹幕文本（danmu）或格式化文本（gift/sc）
                - gift: 礼物信息（gift 类型）
                - sc: SC 信息（sc 类型）
        """
        if not self._initialized:
            logger.debug("RewardTracker 未初始化，跳过")
            return

        if not messages:
            return

        for msg in messages:
            await self._process_single_message(msg)

    async def _process_single_message(self, msg: Dict[str, Any]):
        """处理单条消息的奖励信号"""
        msg_type = msg.get("type", "danmu")
        username = msg.get("name", msg.get("username", "匿名"))
        content = msg.get("content", "")

        if msg_type == "gift":
            await self._handle_gift(msg, username, content)
        elif msg_type == "sc":
            await self._handle_sc(msg, username, content)
        elif msg_type == "danmu":
            await self._handle_danmu(content, username)
        # 其他类型（system/ai_response 等）不产生奖励信号

    async def _handle_danmu(self, content: str, username: str):
        """处理弹幕：情感打分"""
        if not content:
            return

        # 记录弹幕时间戳（密度追踪）
        self._density_tracker.record()

        # 情感打分
        sentiment = score_danmu(content)
        score = sentiment["score"]

        if score == 0:
            return  # 中性弹幕，不产生奖励事件

        source = "danmu_pos" if score > 0 else "danmu_neg"
        self._stats[source] = self._stats.get(source, 0) + 1
        self._stats["total_events"] += 1

        event = RewardEvent.create(
            score=score,
            source=source,
            content=content[:100],  # 截断避免 store 膨胀
            username=username[:50],
            metadata={
                "confidence": sentiment["confidence"],
                "positive_hits": sentiment["positive_hits"],
                "negative_hits": sentiment["negative_hits"],
            },
        )
        await self._store_and_aggregate(event)

    async def _handle_gift(self, msg: Dict[str, Any], username: str, content: str):
        """处理礼物"""
        gift_info = msg.get("gift", {})
        gift_name = gift_info.get("name", "") or content
        gift_num = gift_info.get("num", 1)

        # 估算电池值
        battery_per_unit = _GIFT_BATTERY_ESTIMATE.get(gift_name, _DEFAULT_GIFT_BATTERY)
        total_battery = battery_per_unit * gift_num

        # 电池值 → 奖励分（10电池 = 1分）
        score = max(1, total_battery // 10)

        self._stats["gift"] += 1
        self._stats["total_events"] += 1

        event = RewardEvent.create(
            score=score,
            source="gift",
            content=f"{gift_name} x{gift_num}",
            username=username[:50],
            metadata={
                "gift_name": gift_name,
                "gift_num": gift_num,
                "battery_per_unit": battery_per_unit,
                "total_battery": total_battery,
            },
        )
        await self._store_and_aggregate(event)
        logger.info(f"[奖励] 礼物 +{score}分: {username} → {gift_name}x{gift_num} (估{total_battery}电池)")

    async def _handle_sc(self, msg: Dict[str, Any], username: str, content: str):
        """处理 SC 醒目留言"""
        sc_info = msg.get("sc", {})
        # SC 的价格信息可能在不同字段里
        price = sc_info.get("price", sc_info.get("amount", sc_info.get("money", 0)))
        # 如果 price 是元，转为分；如果已经是分就直接用
        if price <= 100:  # 大概率是元
            score = price * 10
        else:
            score = price

        self._stats["sc"] += 1
        self._stats["total_events"] += 1

        event = RewardEvent.create(
            score=score,
            source="sc",
            content=content[:100],
            username=username[:50],
            metadata={
                "sc_price": price,
            },
        )
        await self._store_and_aggregate(event)
        logger.info(f"[奖励] SC +{score}分: {username} ({price}分)")

    # ============ 与 AI 回复绑定 ============

    def bind_to_response(self, response_text: str):
        """
        将后续的奖励事件与当前 AI 回复绑定

        在 AI 回复生成完成时调用（由 main.py 的 _llm_process_node 触发）。
        每次调用会 finalize 上一个 aggregator，开启新的。
        """
        # finalize 上一个（如果存在）
        if self._current_aggregator is not None:
            agg_result = self._current_aggregator.finalize()
            self._remember_aggregation(agg_result)

        # 开启新的
        self._current_aggregator = ResponseRewardAggregator(
            response_text=response_text,
            response_timestamp=time.time(),
        )
        logger.info(f"[奖励追踪] 开始追踪回复: {response_text[:50]}...")

    async def finalize_current_response(self):
        """
        finalize 当前 aggregator 并持久化

        可由定时器在 AI 回复后 30s 调用，也可在新回复开始时自动触发。
        """
        if self._current_aggregator is None:
            return

        # 检查弹幕密度（隐式奖励）
        density = self._density_tracker.get_density_delta(
            self._current_aggregator.response_timestamp
        )
        if density["density_spike"]:
            spike_event = RewardEvent.create(
                score=1,
                source="density_spike",
                content=f"弹幕密度突增 {density['ratio']:.1f}x ({density['after_count']}条/窗)",
                metadata=density,
            )
            self._current_aggregator.add_event(spike_event)
            logger.info(f"[奖励] 弹幕密度突增 +1分: {density['ratio']:.1f}x")

        # finalize 并存储
        agg_result = self._current_aggregator.finalize()
        self._remember_aggregation(agg_result)
        self._current_aggregator = None

        # 持久化
        if self._memory_manager and agg_result.get("total_score", 0) != 0:
            try:
                await self._memory_manager.save_reward_aggregation(agg_result)
                tag = "🟢" if agg_result["total_score"] > 0 else "🔴"
                logger.info(
                    f"[奖励聚合] {tag} score={agg_result['total_score']:+d}, "
                    f"events={agg_result['event_count']}, "
                    f"high={agg_result['is_high_reward']}, low={agg_result['is_low_reward']}, "
                    f"回复={agg_result['response_text'][:40]}..."
                )
            except Exception as e:
                logger.error(f"[奖励] 持久化聚合结果失败: {e}")

    def _remember_aggregation(self, agg_result: Dict[str, Any]):
        """缓存最近的聚合结果（供快速查询，不依赖 store）"""
        self._recent_aggregations.append(agg_result)
        if len(self._recent_aggregations) > self._max_recent:
            self._recent_aggregations = self._recent_aggregations[-self._max_recent:]

    async def _store_and_aggregate(self, event: Dict[str, Any]):
        """存储单条奖励事件 + 添加到当前 aggregator"""
        # 添加到当前 aggregator
        if self._current_aggregator is not None:
            self._current_aggregator.add_event(event)

        # 持久化单条事件
        if self._memory_manager:
            try:
                await self._memory_manager.save_reward_event(event)
            except Exception as e:
                logger.error(f"[奖励] 存储事件失败: {e}")

    # ============ 手动评分接口 ============

    async def manual_score(self, score: int, reason: str = "", username: str = "operator"):
        """
        手动评分（由 WebUI 运营者触发）

        Args:
            score: +5 好评 / -5 差评（可自定义）
            reason: 评分理由
            username: 运营者用户名
        """
        if self._current_aggregator is None:
            logger.warning("手动评分：当前没有活跃的回复在追踪")
            return

        event = RewardEvent.create(
            score=score,
            source="manual",
            content=reason[:100],
            username=username[:50],
            metadata={"operator_action": True},
        )
        self._stats["manual"] += 1
        self._stats["total_events"] += 1
        await self._store_and_aggregate(event)
        logger.info(f"[手动评分] {score:+d}分: {reason}")

    # ============ 统计查询 ============

    def get_stats(self) -> Dict[str, Any]:
        """获取当前奖励统计"""
        return dict(self._stats)

    def get_recent_high_reward(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的高奖励回复"""
        return [
            agg for agg in reversed(self._recent_aggregations)
            if agg.get("is_high_reward", False)
        ][:limit]

    def get_recent_low_reward(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的低奖励回复"""
        return [
            agg for agg in reversed(self._recent_aggregations)
            if agg.get("is_low_reward", False)
        ][:limit]


# ============ 全局单例便捷函数 ============

_tracker_instance: Optional[RewardTracker] = None


def get_reward_tracker() -> RewardTracker:
    """获取全局 RewardTracker 实例"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = RewardTracker.get_instance()
    return _tracker_instance


async def init_reward_tracker(memory_manager=None):
    """初始化 RewardTracker（绑定 MemoryManager）"""
    tracker = get_reward_tracker()
    await tracker.initialize(memory_manager)
    return tracker
