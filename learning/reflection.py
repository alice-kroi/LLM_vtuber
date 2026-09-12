#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆反思模块（Memory Reflection）

对应方案「记忆系统升级 A2：记忆反思压缩」。
仿照 Generative Agents 的 memory reflection：
定期对近期积累的高奖励互动（L4 经验）做一次**反思**，
提炼出关于「观众偏好 / 互动策略 / 人设表现」的概括性认知（L3 语义记忆），
存回 knowledge_store 供后续检索注入。

设计原则：
- 异步、不阻塞直播主流程
- 用真实豆包 LLM 提炼（复用现有调用，无需新模型）
- 提炼结果低置信度不注入，防止错误认知污染人设
- 有节流：避免频繁触发浪费 token
"""

import asyncio
import logging
import os
import time
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class MemoryReflection:
    """
    记忆反思器

    从 knowledge_store 的 L4 经验记忆（高奖励回复）中，
    用 LLM 提炼出一句话级别的概括性洞察。
    """

    _instance: Optional["MemoryReflection"] = None

    def __init__(
        self,
        memory_manager=None,
        model: str = "doubao-seed-1-8-251228",
        max_insights_per_run: int = 3,
        interval_minutes: float = 30.0,
        min_reflect_score: int = 5,
        enabled: bool = True,
    ):
        self.memory_manager = memory_manager
        self.model = model
        self.max_insights = max_insights_per_run
        self.interval = interval_minutes * 60.0
        self.min_reflect_score = min_reflect_score
        self.enabled = enabled
        self._last_reflect_ts: float = 0.0
        self._seen_texts: set = set()  # 避免重复提炼相同内容

    @classmethod
    def get_instance(cls, **kwargs) -> "MemoryReflection":
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    def set_config(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ============ 主入口 ============

    async def reflect(self, force: bool = False) -> Dict[str, Any]:
        """
        执行一次记忆反思

        1. 从 knowledge_store 取 L4 经验（高奖励回复 pattern）
        2. 用 LLM 提炼概括性洞察
        3. 存回 knowledge_store（memory_layer=L3_semantic）

        Returns:
            {"insights": int, "skipped": bool, "reason": str}
        """
        now = time.time()
        if not force and (now - self._last_reflect_ts) < self.interval:
            return {"insights": 0, "skipped": True,
                    "reason": f"距上次反思不足 {self.interval/60:.0f} 分钟"}

        if not self.enabled:
            return {"insights": 0, "skipped": True, "reason": "reflection_disabled"}

        if self.memory_manager is None:
            return {"insights": 0, "skipped": True, "reason": "no_memory_manager"}

        # 1. 取 L4 经验记忆
        patterns = await self.memory_manager.get_knowledge_items(
            min_reward_score=self.min_reflect_score, limit=20
        )
        # 过滤掉已见过的
        new_patterns = [p for p in patterns if p.get("text", "") not in self._seen_texts]
        if not new_patterns:
            return {"insights": 0, "skipped": True, "reason": "no_new_patterns"}

        # 2. LLM 提炼
        insights = await self._llm_synthesize(new_patterns)
        saved = 0
        for insight in insights[:self.max_insights]:
            text = insight.get("text", "").strip()
            if not text:
                continue
            ok = await self.memory_manager.save_knowledge_item({
                "text": text,
                "reward_score": 0,
                "confidence_score": min(float(insight.get("confidence", 0.5)), 1.0),
                "source": "memory_reflection",
                "knowledge_type": "semantic_insight",
                "memory_layer": "L3_semantic",
                "reason": "反思提炼的观众偏好/互动策略认知",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "retrieval_count": 0,
            })
            if ok:
                saved += 1
            self._seen_texts.add(text)

        # 标记所有已处理的 pattern
        for p in new_patterns:
            self._seen_texts.add(p.get("text", ""))

        self._last_reflect_ts = now
        logger.info(f"[记忆反思] 完成：提炼 {saved} 条语义洞察")
        return {"insights": saved, "skipped": False}

    async def _llm_synthesize(self, patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        用豆包 LLM 从高奖励回复中提炼概括性洞察

        Returns:
            [{"text": str, "confidence": float}, ...]
        """
        api_key = os.getenv("Doubao_API_KEY", os.getenv("DOUBAO_API_KEY", ""))
        api_url = os.getenv("Doubao_API_URL", "")
        if not api_key or not api_url:
            logger.warning("[记忆反思] 未配置豆包 API，跳过反思提炼")
            return []

        # 构造要提炼的样本
        sample_lines = []
        for i, p in enumerate(patterns[:8], 1):
            txt = p.get("text", "")
            score = p.get("reward_score", 0)
            reason = p.get("reason", "")
            if txt:
                sample_lines.append(f"{i}. [{+score}分][{reason}] {txt}")
        if not sample_lines:
            return []

        prompt = (
            "你是直播运营分析助手。以下是虚拟主播近期获得观众好评的回复片段。"
            "请提炼 2-3 条概括性的观众偏好或互动策略洞察（每条一句话，不谈具体回复内容）。\n\n"
            f"{chr(10).join(sample_lines)}\n\n"
            "严格输出 JSON 数组，格式："
            '[{"insight":"洞察内容","confidence":0.5}]，只输出 JSON。'
        )

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=api_url)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.7,
            )
            raw = resp.choices[0].message.content.strip()
            # 提取 JSON 数组
            import json
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                raise ValueError("LLM 未返回合法 JSON")
            data = json.loads(raw[start:end + 1])
            return [{"text": d.get("insight", ""), "confidence": d.get("confidence", 0.5)}
                    for d in data if d.get("insight")]
        except Exception as e:
            logger.warning(f"[记忆反思] LLM 提炼失败: {e}")
            return []

    # ============ 便捷 ============

    async def run_periodic(self):
        """周期性反思入口（可在后台协程调用）"""
        if not self.enabled:
            return
        try:
            result = await self.reflect()
            if not result.get("skipped"):
                logger.info(f"[记忆反思] 周期任务: {result}")
        except Exception as e:
            logger.error(f"[记忆反思] 周期任务异常: {e}")


# ============ 全局单例 ============

_reflection_instance: Optional[MemoryReflection] = None


def get_memory_reflection(**kwargs) -> MemoryReflection:
    """获取全局 MemoryReflection 实例"""
    global _reflection_instance
    if _reflection_instance is None:
        _reflection_instance = MemoryReflection.get_instance(**kwargs)
    return _reflection_instance