# ============================================================
# persona/memory/summarize.py — 轮次摘要 / 候选事实抽取
# ============================================================
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = """你是记忆整理器。只根据用户问题和最终回答，生成一条自包含摘要，并提取可能长期有效的候选事实。

只输出 JSON，不要代码块：
{
  "summary": "一句话摘要，<=120字，自包含，不带日期前缀",
  "entities": ["关键实体"],
  "importance": 0.0,
  "candidate_facts": [
    {"kind": "profile|global_fact|project_key|rule", "text": "事实本身", "reason": "为什么值得长期记住", "confidence": 0.0}
  ]
}

规则：
- 摘要只写用户问了什么、最终结论是什么；不要复述工具调用过程、思考过程。
- 候选事实必须来自用户明确表达；不要从助手回答、推论或上下文猜想来提取。
- 不要提取关于记忆系统、工具用法、助手能力或本次操作过程的规则。
- 候选事实最多 2 条；一次性任务、临时错误、代码现状描述、密钥/隐私、重复内容都不要提。
- 偏好至少是稳定偏好；单次行为不算偏好。同一事实只保留一条，合并相近表述。
- 不确定就不提；宁缺毋滥。
"""

_ALLOWED_KINDS = {"profile", "global_fact", "project_key", "rule"}


def _parse_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    candidate = raw[start:end + 1]
    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            data = json.loads(attempt)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            continue
    return {}


def fallback_summary(user_text: str, assistant_text: str) -> Dict[str, Any]:
    u = re.sub(r"\s+", " ", str(user_text or "")).strip()
    a = re.sub(r"\s+", " ", str(assistant_text or "")).strip()
    summary = f"用户：{u[:70]}；回答：{a[:90]}"
    return {
        "summary": summary[:160],
        "entities": [],
        "importance": 0.4,
        "candidate_facts": [],
    }


async def summarize_turn(user_text: str, assistant_text: str,
                         purpose: str = "general") -> Dict[str, Any]:
    try:
        from LLM_v2.core.llm_adapter import get_llm_for
        llm = get_llm_for(purpose)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[memory] 摘要模型不可用: {e}")
        return fallback_summary(user_text, assistant_text)

    user_payload = (
        "【用户问题】\n" + str(user_text or "")[:1800]
        + "\n\n【最终回答】\n" + str(assistant_text or "")[:2200]
    )
    try:
        res = await llm.chat(
            system_prompt=SUMMARY_SYSTEM,
            user_text=user_payload,
            history=[],
            remember=False,
            max_tokens=700,
            temperature=0.1,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[memory] 摘要调用失败: {e}")
        return fallback_summary(user_text, assistant_text)

    if res.get("error"):
        logger.debug(f"[memory] 摘要模型错误: {res['error']}")
        return fallback_summary(user_text, assistant_text)

    data = _parse_json(res.get("response") or "")
    if not data:
        return fallback_summary(user_text, assistant_text)

    summary = str(data.get("summary") or "").strip()
    if not summary:
        summary = fallback_summary(user_text, assistant_text)["summary"]

    try:
        importance = float(data.get("importance", 0.5))
    except Exception:  # noqa: BLE001
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    entities: List[str] = []
    for item in data.get("entities") or []:
        text = str(item).strip()
        if text and text not in entities:
            entities.append(text[:40])
    entities = entities[:8]

    facts: List[Dict[str, Any]] = []
    for item in data.get("candidate_facts") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        if kind not in _ALLOWED_KINDS or not text:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except Exception:  # noqa: BLE001
            confidence = 0.5
        facts.append({
            "kind": kind,
            "text": text[:300],
            "reason": str(item.get("reason") or "")[:200],
            "confidence": max(0.0, min(1.0, confidence)),
        })
        if len(facts) >= 2:
            break

    return {
        "summary": summary[:240],
        "entities": entities,
        "importance": importance,
        "candidate_facts": facts,
    }


__all__ = ["summarize_turn", "fallback_summary"]
