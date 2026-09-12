#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
思考方式升级模块（Reasoning Agent）

对应方案「思考方式升级 B2/S6：Plan-and-Execute」与「B1/S5：ReAct 自我反思」。

默认不启用（保持直播低延迟），通过 `thinking_mode` 开关启用后：
- S6 Plan-and-Execute：复杂任务先生成步骤计划再执行
- S5 ReAct 反思：对最终回答做一次自评，偏离人设/不满足时二次修正（限 1 次）

设计原则：
- 每个函数都是独立可测的（真实豆包 LLM 调用）
- 启用成本 = 额外 2 次 LLM 调用（1 计划 + 1 反思），仅在 thinking_mode 下产生
"""

import json
import logging
import os
from typing import Dict, Any, List, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)

PLANNER_MODEL = "doubao-seed-1-8-251228"
REFLECTOR_MODEL = "doubao-seed-1-8-251228"


class ReasoningAgent:
    """
    思考代理：提供计划生成与答案反思能力
    """

    def __init__(
        self,
        model: str = PLANNER_MODEL,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        # P2 预算化思考（Budget Forcing）
        max_plan_tokens: int = 150,
        max_reflect_tokens: int = 120,
    ):
        self.model = model
        self._client = None
        self._api_key = api_key or os.getenv("Doubao_API_KEY", os.getenv("DOUBAO_API_KEY", ""))
        self._api_url = api_url or os.getenv("Doubao_API_URL", "")
        # 思考预算：限制规划/反思的 token 上限，防"过度思考"
        self.max_plan_tokens = max_plan_tokens
        self.max_reflect_tokens = max_reflect_tokens

    def set_think_budget(self, max_plan_tokens: int = None, max_reflect_tokens: int = None):
        """运行时调整思考预算"""
        if max_plan_tokens is not None:
            self.max_plan_tokens = max(int(max_plan_tokens), 32)
        if max_reflect_tokens is not None:
            self.max_reflect_tokens = max(int(max_reflect_tokens), 32)

    def _plan_truncate(self, text: str, budget_chars: int = 300) -> str:
        """早停/兜底：把计划压缩到预算内，避免长思考挤占上下文"""
        if not text:
            return text
        if len(text) <= budget_chars:
            return text
        return text[:budget_chars - 4] + "…"

    def _get_client(self):
        if self._client is None:
            if OpenAI is None:
                raise RuntimeError("openai 库未安装")
            self._client = OpenAI(api_key=self._api_key, base_url=self._api_url)
        return self._client

    # ============ S6：Plan-and-Execute ============

    def plan_task(self, question: str, system_prompt: str = "") -> str:
        """
        为复杂任务生成步骤计划（计划由 LLM 产出）

        Args:
            question: 用户问题
            system_prompt: 主播人设提示词（可选）

        Returns:
            计划字符串（含编号步骤），失败时返回空串
        """
        if not self._api_key or not self._api_url:
            return ""
        system = (
            "你是虚拟主播的规划助手。把用户的请求拆解成 2-4 个可执行的简短步骤。"
            "只输出步骤编号，不要输出思考过程，步骤要贴合" + (system_prompt[:200] or "主播人设") + "。"
        )
        prompt = f"用户请求：{question}\n请给出执行计划。"
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_plan_tokens,  # P2 预算化思考：限制思考 token
                temperature=0.4,
            )
            # 早停兜底：无论模型输出多长，计划都压到预算内
            return self._plan_truncate((resp.choices[0].message.content or "").strip())
        except Exception as e:
            logger.warning(f"[Reasoning] 计划生成失败: {e}")
            return ""

    # ============ S5：ReAct 自我反思 ============

    def reflect_answer(
        self, question: str, answer: str, persona: str = ""
    ) -> Dict[str, Any]:
        """
        对最终回答做自评，判断是否满意（是否偏离人设/是否充分回应）

        Args:
            question: 用户问题
            answer: 生成的回答
            persona: 主播人设简介

        Returns:
            {"satisfied": bool, "suggestion": str}
            satisfied=False 且有 suggestion 时调用方可重生成
        """
        if not answer:
            return {"satisfied": True, "suggestion": ""}
        if not self._api_key or not self._api_url:
            return {"satisfied": True, "suggestion": ""}

        person_line = f"主播人设：{persona[:150]}" if persona else "主播人设为活泼可爱的二次元虚拟主播"
        prompt = (
            f"用户问题：{question}\n"
            f"AI 回答：{answer}\n"
            f"{person_line}\n\n"
            "请评估这个回答是否：(1)贴合人设语气口语化；(2)充分回应了用户问题。"
            '严格输出 JSON：{"satisfied":true/false,"suggestion":"若不满意给1条20字内的改进建议，满意则空"}'
        )
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是直播质量评估助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=120,
                temperature=0.3,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # 提取 JSON
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return {"satisfied": True, "suggestion": ""}
            data = json.loads(raw[start:end + 1])
            return {
                "satisfied": bool(data.get("satisfied", True)),
                "suggestion": data.get("suggestion", ""),
            }
        except Exception as e:
            logger.debug(f"[Reasoning] 反思评估失败: {e}")
            return {"satisfied": True, "suggestion": ""}


# 全局单例
_instance: Optional[ReasoningAgent] = None


def get_reasoning_agent(**kwargs) -> ReasoningAgent:
    global _instance
    if _instance is None:
        _instance = ReasoningAgent(**kwargs)
    return _instance