# ============================================================
# persona/learning/reflect.py — 经验反思（distilled）
# ============================================================
# 把"同类心境下某动作的历史结果"压成一句可执行的内心经验。
#   · 有 LLM：让它用 ≤30 字总结下次该怎么做
#   · 无 LLM：按成功率给出规则句（保证降级可用）
#   · 结果写 learn_reflections(scope='will')，不另建表
#
# 触发方：WillEngineBrain 在任务结果回填后（每小时最多一次）。
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_ATTEMPTS = 3
MAX_DISTILLED_CHARS = 60


async def distill_will_experience(soul, llm=None,
                                  min_attempts: int = MIN_ATTEMPTS
                                  ) -> List[Dict[str, Any]]:
    """为当前状态签名下的动作生成 distilled；已是最新则跳过"""
    signature = soul.state_signature()
    stats = soul.experience_stats(signature)
    existing = soul.distilled_map(signature)
    created: List[Dict[str, Any]] = []

    for stat in stats:
        attempts = int(stat.get("attempts") or 0)
        if attempts < min_attempts:
            continue
        prev = existing.get(stat["action_type"]) or {}
        if int(prev.get("attempts") or 0) == attempts:
            continue          # 结果没有新增，不重复反思

        text = await _distill_one(stat, llm)
        if not text:
            continue
        rate = stat["successes"] / attempts if attempts else 0.0
        if soul.record_distilled(signature, stat["action_type"], text, attempts, rate):
            created.append({"action_type": stat["action_type"],
                            "distilled": text, "attempts": attempts})
            logger.info(f"[reflect] {stat['action_type']} → {text}")
    return created


async def _distill_one(stat: Dict[str, Any], llm=None) -> str:
    attempts = int(stat.get("attempts") or 0)
    successes = int(stat.get("successes") or 0)
    avg = float(stat.get("avg_outcome") or 0.0)

    if llm is not None:
        try:
            prompt = (
                f"动作类型：{stat['action_type']}\n"
                f"同类心境下：尝试 {attempts} 次，成功 {successes} 次，"
                f"平均结果 {avg:+.2f}（-1 最差，+1 最好）。\n"
                "用一句不超过 30 字的中文总结：下次遇到同类心境，该不该做这个动作、"
                "要注意什么。只输出这一句话。"
            )
            response = await llm.chat_simple(
                system_prompt="你是维尔薇的内心复盘器。只输出一句话，不要解释。",
                user_text=prompt,
            )
            text = (response or "").strip().splitlines()[0].strip(' "\'“”')
            if text:
                return text[:MAX_DISTILLED_CHARS]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[reflect] LLM 反思失败，回落规则: {e}")

    rate = successes / attempts if attempts else 0.0
    if rate >= 0.7:
        return "这类动作通常能成，下次可以优先考虑。"
    if rate <= 0.4:
        return "这类动作经常失败，下次先换别的。"
    return "这类动作结果不稳定，谨慎尝试。"


__all__ = ["distill_will_experience", "MIN_ATTEMPTS"]
