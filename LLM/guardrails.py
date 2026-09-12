#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 Guardrails 输出护栏

对 LLM 生成的回复做轻量规则校验与清洗，防止：
- 空回复 / 超长回复
- 含危险/违规内容（敏感关键词）
- 混入不应出现的思考痕迹 / 控制字符

设计原则：
- 纯规则实现（正则/长度/黑名单），<1ms，不引入额外 LLM 延迟
- 幂等、可单测
- 输出统一结构 {"passed": bool, "text": str, "reason": str}
"""

import logging
import re
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 敏感/违规内容关键词（保守、明确违规，避免误伤正常互动）
BLOCKED_KEYWORDS = [
    "裸聊", "约炮", "赌场", "博彩下注", "洗钱", "贩卖毒品", "枪支", "自杀方法", "明星生父",
    "私处", "援交", "代孕", "器官买卖", "贩卖儿童",
]

# 常见思考痕迹（应删除，不泄露思考过程）
REASONING_MARKS = [
    "作为AI", "作为智能体", "我的思考是", "思路如下", "推理过程", "综上所述,我考虑",
    "让我想想", "首先我需要", "我将按照",
]

MAX_LEN = 220  # 硬上限（宽松于主播100字约束，护栏作为最终防线）


def _has_blocked(text: str) -> bool:
    for kw in BLOCKED_KEYWORDS:
        if kw in text:
            return True
    return False


def _strip_reasoning(text: str) -> str:
    """删除思考痕迹句子与思维链残留"""
    for mark in REASONING_MARKS:
        idx = text.find(mark)
        if idx != -1:
            # 删到该句段结束（到下一个标点或句尾）
            seg = text[idx:]
            end = min([i for i in (seg.find("。"), seg.find("！"), seg.find("？"), seg.find("\n")) if i != -1] + [len(seg)])
            text = text[:idx] + seg[end:]
    # 删除常见"XML/伪markdown"残留
    text = re.sub(r"<\w+>|</\w+>", "", text)
    return text.strip()


def _clean_control(text: str) -> str:
    cleaned = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in "\n\t":
            continue  # 非法控制字符
        cleaned.append(ch)
    return "".join(cleaned)


def guard_reply(text: str, max_len: int = MAX_LEN) -> Dict[str, Any]:
    """
    对回复做护栏校验与清洗。

    Returns:
        {"passed": bool, "text": str, "reason": str}
        - passed=False 表示应阻止使用该回复（空/违规/超长未可截）
        - text 为清洗后的文本
    """
    if not text or not text.strip():
        return {"passed": False, "text": "", "reason": "empty"}

    text = _clean_control(text)

    if _has_blocked(text):
        logger.warning(f"[护栏] 检测到违规关键词，拦截回复: {text[:40]}...")
        return {"passed": False, "text": "", "reason": "blocked_keyword"}

    # 删除思考痕迹
    stripped = _strip_reasoning(text)
    if stripped != text:
        logger.debug("[护栏] 已移除思考痕迹")
        text = stripped

    # 超长：截断到预算（保留语义，加省略号）
    if len(text) > max_len:
        logger.info(f"[护栏] 回复超长({len(text)}字)，截断至 {max_len}")
        text = text[:max_len - 4] + "……"

    return {"passed": True, "text": text, "reason": "ok"}


# 全局便捷
def guard_reply_safe(text: str, max_len: int = MAX_LEN) -> str:
    """护栏清洗并返回安全文本（未通过则返回空串），供主流程便捷调用"""
    r = guard_reply(text, max_len=max_len)
    return r["text"] if r["passed"] else ""