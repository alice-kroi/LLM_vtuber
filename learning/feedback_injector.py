#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反馈注入模块

在 LLM 生成回复前，把历史高奖励案例（来自 reward_store）
注入到 system_prompt 中作为 few-shot 参考，实现"自主学习"。

设计原则：
- 注入要轻量：每条案例压缩到 200 字以内，总共最多 3 条，避免 prompt 膨胀
- 有回退：没有高奖励样本时不注入，不影响正常流程
- 可关闭：通过 config.ini 配置开关
"""

import asyncio
import logging
import time
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class FeedbackInjector:
    """
    反馈注入器

    职责：
    1. 从 MemoryManager 拉取高奖励回复样本
    2. 格式化为 few-shot prompt 片段
    3. 追加到 system_prompt 末尾
    """

    _instance: Optional["FeedbackInjector"] = None

    def __init__(
        self,
        min_score: int = 5,
        max_examples: int = 3,
        since_hours: float = 24.0,
        enabled: bool = True,
    ):
        """
        Args:
            min_score: 最低奖励分阈值（总分 >= 此值才算"好回复"）
            max_examples: 最多注入几条案例
            since_hours: 只取最近 N 小时内的数据
            enabled: 是否启用（可在 WebUI 动态关闭）
        """
        self.min_score = min_score
        self.max_examples = max_examples
        self.since_hours = since_hours
        self.enabled = enabled

        # 缓存 + 刷新控制（避免每条弹幕都去 store 查询）
        self._cache: List[Dict[str, Any]] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0  # 5 分钟刷新一次

    @classmethod
    def get_instance(cls, **kwargs) -> "FeedbackInjector":
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    def set_enabled(self, enabled: bool):
        """动态启用/关闭"""
        self.enabled = enabled
        logger.info(f"[反馈注入] {'启用' if enabled else '关闭'}")

    def set_config(self, **kwargs):
        """动态调整参数"""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        # 参数变更后清空缓存
        self._cache = []
        self._cache_ts = 0.0

    # ============ 主入口 ============

    async def build_injection_prompt(
        self,
        memory_manager=None,
        session_id: str = "default",
        force_refresh: bool = False,
    ) -> Optional[str]:
        """
        构建注入到 system_prompt 的 few-shot 文本（正向 + 反向）

        正向：获得好评的回复风格（few-shot 模仿）
        反向：S11 记录的不受欢迎模式（提醒避免）

        Args:
            memory_manager: MemoryManager 实例
            session_id: 会话标识
            force_refresh: 强制刷新缓存

        Returns:
            格式化的 prompt 片段，或 None（无样本或未启用）
        """
        if not self.enabled:
            return None

        if memory_manager is None:
            return None

        # 检查缓存
        now = time.time()
        if not force_refresh and (now - self._cache_ts) < self._cache_ttl and self._cache:
            examples = self._cache
        else:
            # 从 store 拉取
            try:
                examples = await memory_manager.get_high_reward_examples(
                    min_score=self.min_score,
                    limit=self.max_examples,
                    session_id=session_id,
                    since_hours=self.since_hours,
                )
                self._cache = examples
                self._cache_ts = now
                logger.info(
                    f"[反馈注入] 从 store 拉取到 {len(examples)} 条高奖励案例 "
                    f"(min_score={self.min_score}, since_hours={self.since_hours})"
                )
            except Exception as e:
                logger.warning(f"[反馈注入] 拉取样本失败: {e}")
                return None

        parts = []
        # S11：反向避免注入（不受 5 分钟缓存影响，每次检查）
        if memory_manager is not None:
            try:
                avoid_examples = await memory_manager.get_knowledge_items(
                    session_id=session_id,
                    since_hours=24.0,
                    min_reward_score=-999,  # 全取，下面过滤 type
                    limit=5,
                )
                avoid = [a for a in avoid_examples if a.get("knowledge_type") == "avoid_pattern"]
                if avoid:
                    avoid_part = self._format_avoid(avoid)
                    if avoid_part:
                        parts.append(avoid_part)
            except Exception as e:
                logger.debug(f"[反馈注入] 避免样本拉取失败: {e}")

        # 正向 few-shot
        if examples:
            pos_part = self._format_few_shot(examples)
            if pos_part:
                parts.append(pos_part)

        if not parts:
            return None

        return "\n".join(parts)

    def _format_avoid(self, avoid: List[Dict[str, Any]]) -> Optional[str]:
        """格式化「避免模式」注入片段"""
        if not avoid:
            return None
        lines = [
            "",
            "## 以下回复方式观众不太喜欢，请不要模仿（尽量避免）",
        ]
        for i, a in enumerate(avoid[:3], 1):
            text = (a.get("text") or "")[:120]
            reason = (a.get("reason") or "")
            lines.append(f"{i}. 避免类似: {text}")
            if reason:
                lines.append(f"   原因: {reason}")
        return "\n".join(lines)

    def _format_few_shot(self, examples: List[Dict[str, Any]]) -> str:
        """
        将高奖励样本格式化为 few-shot prompt 片段

        格式示例:
        ## 你之前获得观众好评的回复参考（学习模仿这种风格）
        1. 你: 哈哈好可爱呀~主播唱歌也超好听的！🎤
           评分: +9分，观众说: 弹幕好评、收到礼物 棒棒糖 x3
        2. ...
        """
        lines = [
            "",  # 空行分隔
            "## 你之前在类似互动中获得过观众好评，参考以下风格（学习模仿）",
        ]

        for i, ex in enumerate(examples, 1):
            text = ex.get("response_text", "")[:150]
            score = ex.get("total_score", 0)
            reason = ex.get("reason", "")
            lines.append(f"{i}. 你的回复: {text}")
            lines.append(f"   好评分: +{score}（{reason}）")

        lines.append(
            "\n请在接下来的回复中参考以上获得好评的风格，"
            "保持活泼有趣、与观众互动、回应弹幕情绪。"
        )

        return "\n".join(lines)

    # ============ 直接注入 system_prompt 的便捷方法 ============

    async def inject_into_system_prompt(
        self,
        system_prompt: str,
        memory_manager=None,
        session_id: str = "default",
    ) -> str:
        """
        直接将 few-shot 注入到 system_prompt 末尾

        无样本或未启用时返回原 system_prompt 不变。
        """
        injection = await self.build_injection_prompt(memory_manager, session_id)
        if injection:
            return f"{system_prompt}\n{injection}"
        return system_prompt

    def invalidate_cache(self):
        """手动刷新缓存（比如运营者刚录入了高奖励样本）"""
        self._cache = []
        self._cache_ts = 0.0
        logger.info("[反馈注入] 缓存已失效")


# ============ 全局便捷函数 ============

_injector_instance: Optional[FeedbackInjector] = None


def get_feedback_injector(**kwargs) -> FeedbackInjector:
    """获取全局 FeedbackInjector 实例"""
    global _injector_instance
    if _injector_instance is None:
        _injector_instance = FeedbackInjector.get_instance(**kwargs)
    return _injector_instance
