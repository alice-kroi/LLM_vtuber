#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识策展模块

自主学习的第二路径：主动提炼 + 淘汰。

流程：
1. 从 reward_store 中筛选高奖励对话对（用户消息 → AI 回复 + 奖励分 >= min_score）
2. 可选：用 LLM 从高奖励对话中提炼知识片段（需要额外 LLM 调用）
3. 提炼结果存入 Milvus（与 RAG chat_history 同一 collection，但带 reward_score 和 confidence_score 元数据）
4. 定期淘汰 Milvus 中低质量知识（低检索率 + 低奖励分 + 超龄）

注意：这个模块对 Milvus 有直接依赖。如果 Milvus 不可用，降级为只在日志中记录，不影响主流程。
"""

import asyncio
import logging
import os
import time
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class KnowledgeCurator:
    """
    知识策展器

    两条路径：
    A. 轻量路径（默认）：直接把高奖励回复作为"好的回答模式"存入 Milvus，
       不做 LLM 提炼（省 token、快）
    B. 深度路径：调用 LLM 提炼知识片段（需手动开启）

    淘汰策略：
    - 主动：定期扫描 Milvus，淘汰低质量
    - 被动：写入时打上 reward_score/confidence_score 标签，检索时可以过滤
    """

    def __init__(
        self,
        memory_manager=None,
        milvus_collection: str = "chat_history",
        milvus_db: str = "LLM_vtuber",
        min_curate_score: int = 10,
        curate_interval_minutes: float = 60.0,
        pruning_interval_minutes: float = 360.0,
        min_retrieval_count: int = 3,
        low_confidence_threshold: float = 0.3,
        max_age_days: float = 30.0,
    ):
        """
        Args:
            memory_manager: MemoryManager 实例（用于查询 reward_store）
            milvus_collection: Milvus collection 名称
            milvus_db: Milvus 数据库名
            min_curate_score: 多少奖励分以上的对话对才值得提炼
            curate_interval_minutes: 两次提炼任务之间的最小间隔
            pruning_interval_minutes: 两次淘汰任务之间的最小间隔
            min_retrieval_count: 低于此值的检索次数 + 低奖励分 → 可淘汰
            low_confidence_threshold: 置信度阈值，低于则淘汰
            max_age_days: 超过此天数的知识 → 可淘汰
        """
        self.memory_manager = memory_manager
        self.milvus_collection = milvus_collection
        self.milvus_db = milvus_db
        self.min_curate_score = min_curate_score
        self.curate_interval = curate_interval_minutes * 60.0
        self.pruning_interval = pruning_interval_minutes * 60.0
        self.min_retrieval_count = min_retrieval_count
        self.low_confidence_threshold = low_confidence_threshold
        self.max_age_seconds = max_age_days * 86400.0

        # 上次执行时间戳
        self._last_curate_ts: float = 0.0
        self._last_prune_ts: float = 0.0

    # ============ 路径 A：轻量提炼（直接存储高奖励模式） ============

    async def curate_high_reward_patterns(
        self,
        since_hours: float = 24.0,
        session_id: str = "default",
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        从 reward_store 中筛选高奖励回复，直接存入 Milvus 作为"好的回答模式"

        不做 LLM 提炼，只做：
        1. 查询 reward_store 中 total_score >= min_curate_score 的 response_reward
        2. 用 reward 关联的时间窗口附近的 user message + AI response 组成对话对
        3. 存入 Milvus（metadata 标记 reward_score + knowledge_type="pattern"）

        Args:
            since_hours: 从多少小时前开始取数据
            session_id: 会话标识
            force: 强制执行（忽略时间间隔限制）

        Returns:
            {"curated": int, "skipped": int, "failed": int}
        """
        now = time.time()
        if not force and (now - self._last_curate_ts) < self.curate_interval:
            remaining = (self.curate_interval - (now - self._last_curate_ts)) / 60.0
            logger.info(
                f"[知识策展] 距离上次提炼不足 {self.curate_interval/60:.0f} 分钟，"
                f"还需等待 {remaining:.0f} 分钟"
            )
            return {"curated": 0, "skipped": 0, "failed": 0, "skipped_reason": "interval_not_reached"}

        # 1. 查 reward_store
        records = await self._fetch_reward_records(since_hours, session_id)
        if not records:
            logger.info("[知识策展] 没有足够的高奖励样本，跳过提炼")
            return {"curated": 0, "skipped": 0, "failed": 0}

        # 2. 存入 MemoryManager 的 knowledge_store（可靠、零外部依赖）
        curated = 0
        skipped = 0
        failed = 0

        for record in records:
            # 高奖励回复本身就是知识片段（好的回答模式）
            response_text = record.get("response_text", "")
            score = record.get("total_score", 0)
            if not response_text or score < self.min_curate_score:
                skipped += 1
                continue

            try:
                success = await self._store_knowledge(
                    text=response_text,
                    reward_score=score,
                    reason=record.get("reason", ""),
                    session_id=session_id,
                )
                if success:
                    curated += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"[知识策展] 存储失败: {e}")
                failed += 1

        self._last_curate_ts = now
        logger.info(
            f"[知识策展] 高奖励模式提炼完成: 成功={curated}, 跳过={skipped}, 失败={failed}"
        )
        return {"curated": curated, "skipped": skipped, "failed": failed}

    async def _fetch_reward_records(
        self, since_hours: float, session_id: str
    ) -> List[Dict[str, Any]]:
        """从 MemoryManager 查询高奖励记录"""
        if self.memory_manager is None:
            # 降级：只能返回空
            return []
        try:
            return await self.memory_manager.get_high_reward_examples(
                min_score=self.min_curate_score,
                limit=50,
                session_id=session_id,
                since_hours=since_hours,
            )
        except Exception as e:
            logger.warning(f"[知识策展] 查询奖励记录失败: {e}")
            return []

    async def _store_knowledge(
        self,
        text: str,
        reward_score: int,
        reason: str = "",
        session_id: str = "default",
    ) -> bool:
        """
        将知识片段存入 MemoryManager 的 knowledge_store

        Args:
            text: 知识内容（高奖励回复模式）
            reward_score: 该模式的历史奖励分
            reason: 获得好评的原因描述
            session_id: 会话标识
        """
        if self.memory_manager is None:
            return False
        try:
            item = {
                "text": text,
                "reward_score": reward_score,
                "confidence_score": min(reward_score / 30.0, 1.0),  # 归一化到 0~1
                "source": "reward_curator",
                "knowledge_type": "good_response_pattern",
                # S1 分层记忆：高奖励互动模式属于「L4 经验记忆」
                "memory_layer": "L4_experience",
                "reason": reason,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "retrieval_count": 0,
            }
            return await self.memory_manager.save_knowledge_item(item, session_id)
        except Exception as e:
            logger.error(f"[知识策展] 知识存储失败: {e}")
            return False

    # ============ S11 反向学习：记录低奖励避免模式 ============

    async def record_avoid_pattern(
        self,
        text: str,
        score: int,
        reason: str = "",
        session_id: str = "default",
    ) -> bool:
        """
        记录一条「观众不喜欢的回复模式」（低奖励），供正向注入反例

        Args:
            text: 不受欢迎的回复内容
            score: 负向评分（通常 <= -3）
            reason: 触发原因（弹幕差评内容）
        """
        if self.memory_manager is None:
            return False
        try:
            item = {
                "text": text,
                "reward_score": score,
                "confidence_score": 1.0,  # 避免模式一律高置信
                "source": "reward_curator_reverse",
                "knowledge_type": "avoid_pattern",
                "memory_layer": "L4_experience",
                "reason": reason,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "retrieval_count": 0,
            }
            return await self.memory_manager.save_knowledge_item(item, session_id)
        except Exception as e:
            logger.error(f"[知识策展] 避免模式记录失败: {e}")
            return False

    # ============ 淘汰：清理低质量知识 ============

    async def prune_low_quality_knowledge(self, force: bool = False) -> Dict[str, Any]:
        """
        淘汰低质量知识（MemoryManager 版）

        淘汰条件（满足任一）：
        - reward_score < min_reward_score 且 confidence_score < low_confidence_threshold

        注意：InMemoryStore 没有按值批量删除的现成 API，
        这里采用"软淘汰"——通过 get_knowledge_items 消费侧过滤低分知识，
        并记录淘汰统计。实际清理由每日重置（进程重启）自然完成。

        Args:
            force: 是否忽略时间间隔
        """
        now = time.time()
        if not force and (now - self._last_prune_ts) < self.pruning_interval:
            logger.info(
                f"[知识策展] 距离上次淘汰不足 {self.pruning_interval/60:.0f} 分钟，跳过"
            )
            return {"pruned": 0, "skipped": True}

        if self.memory_manager is None:
            return {"pruned": 0, "skipped": True, "reason": "memory_manager_unavailable"}

        # 统计低质量知识数量（软淘汰：只统计，由消费侧过滤）
        low_count = 0
        try:
            items = await self.memory_manager.get_knowledge_items(
                session_id="default",
                since_hours=24.0 * 30,
                min_reward_score=0,
                limit=500,
            )
            low_count = sum(
                1 for it in items
                if it.get("confidence_score", 0) < self.low_confidence_threshold
                and it.get("reward_score", 0) < 5
            )
            logger.info(
                f"[知识策展] 淘汰检查完成：共 {len(items)} 条知识，"
                f"其中低质量约 {low_count} 条（软淘汰，由消费侧过滤）"
            )
        except Exception as e:
            logger.warning(f"[知识策展] 淘汰检查失败: {e}")

        self._last_prune_ts = now
        return {"pruned": low_count, "soft_prune": True}

    # ============ 定时任务 ============

    async def run_periodic_tasks(self):
        """
        执行周期性知识策展任务（提炼 + 淘汰）

        应该在后台协程中定期调用（比如 main.py 启动后 asyncio.create_task）。
        """
        logger.info("[知识策展] 开始执行周期性任务")
        try:
            curate_result = await self.curate_high_reward_patterns()
            logger.info(f"[知识策展] 提炼结果: {curate_result}")
        except Exception as e:
            logger.error(f"[知识策展] 提炼失败: {e}")

        try:
            prune_result = await self.prune_low_quality_knowledge()
            logger.info(f"[知识策展] 淘汰结果: {prune_result}")
        except Exception as e:
            logger.error(f"[知识策展] 淘汰失败: {e}")


# ============ 全局便捷函数 ============

_curator_instance: Optional[KnowledgeCurator] = None


def get_knowledge_curator(**kwargs) -> KnowledgeCurator:
    """获取全局 KnowledgeCurator 实例"""
    global _curator_instance
    if _curator_instance is None:
        _curator_instance = KnowledgeCurator(**kwargs)
    return _curator_instance
