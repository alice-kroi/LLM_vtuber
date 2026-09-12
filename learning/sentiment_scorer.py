#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弹幕情感打分模块

先用正则关键词匹配做轻量打分（<1ms/条），后续可扩展 LLM 打分作为 fallback。

打分规则：
- 正面关键词匹配 → +3
- 负面关键词匹配 → -2  
- 混合（同时匹配正反） → 取差
- 无匹配 → 0

设计原则：快、准、可解释。每条弹幕打分 <1ms，不阻塞主流程。
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ============ 关键词表 ============

# 正面：表达喜欢、赞美、开心
_POSITIVE_KEYWORDS: List[str] = [
    # 直接赞美
    "好听", "声音好", "唱得好", "唱得太棒了", "太棒了", "太牛了", "厉害", "强", "666", "777", "888",
    "可爱", "好可爱", "好萌", "萌", "萌萌哒", "awsl", "爱了", "爱了爱了", "喜欢", "超喜欢",
    "好看", "好帅", "好美", "漂亮",
    "哈哈", "哈哈哈", "笑死", "笑死我了", "太好笑了", "快乐", "开心", "好玩", "有意思",
    "神", "神了", "神操作", "天花板", "绝", "绝了", "yyds",
    # 感谢/支持
    "辛苦了", "加油", "冲", "冲冲冲", "支持", "给你打call", "笔芯", "么么哒", "mua",
    # 互动反馈
    "对", "没错", "说得好", "同意", "有理", "确实", "就是",
    # 网络用语正面
    "nb", "NB", "牛", "顶", "赞", "👍", "👏", "♥", "♡",
]

# 负面：表达无聊、不满、尴尬
_NEGATIVE_KEYWORDS: List[str] = [
    "无聊", "好无聊", "没意思", "不好玩",
    "不好听", "难听", "声音不好",
    "尴尬", "好尬", "尴尬死了",
    "无语", "汗", "晕倒", "orz", "0.0",
    "太菜了", "菜", "坑", "菜鸡",
    "水", "划水", "摸鱼",
    "走了", "下了", "撤了", "溜了", "跑了", "没意思走了",
    "退了", "退了退了",
]

# 中性偏负面（比"无聊"弱，主要表达迷茫/不知道说啥）
_NEUTRAL_NEGATIVE: List[str] = [
    "这是啥", "这什么", "什么鬼",
    "？？？", "???", "……", "。。。", "...",
    "沉默",
]


# ============ 编译正则（预编译提速） ============

def _compile_pattern(keywords: List[str]) -> re.Pattern:
    """将关键词列表编译为一个正则，用 | 连接"""
    if not keywords:
        return re.compile(r'$^')  # 永远不匹配的正则
    # 按长度倒序排列，长词优先匹配（避免"哈哈"吃掉"哈哈哈"）
    sorted_kws = sorted(set(keywords), key=len, reverse=True)
    escaped = [re.escape(kw) for kw in sorted_kws]
    return re.compile('|'.join(escaped), re.IGNORECASE)


_POS_PATTERN = _compile_pattern(_POSITIVE_KEYWORDS)
_NEG_PATTERN = _compile_pattern(_NEGATIVE_KEYWORDS)
_NEUTRAL_NEG_PATTERN = _compile_pattern(_NEUTRAL_NEGATIVE)


# ============ 主接口 ============

def score_danmu(text: str) -> Dict[str, Any]:
    """
    对一条弹幕文本进行情感打分

    Args:
        text: 弹幕内容

    Returns:
        {
            "score": int,          # 综合得分: 正数=正面, 负数=负面, 0=中性
            "positive_hits": [...], # 命中的正面关键词
            "negative_hits": [...], # 命中的负面关键词
            "neutral_neg_hits":[...],
            "confidence": float,    # 置信度 0~1（命中越多越高）
        }
    """
    result = {
        "score": 0,
        "positive_hits": [],
        "negative_hits": [],
        "neutral_neg_hits": [],
        "confidence": 0.0,
    }

    if not text or not text.strip():
        return result

    text_stripped = text.strip()

    # 1. 正面匹配
    pos_hits = _POS_PATTERN.findall(text_stripped)
    # 去重（同一条弹幕可能重复命中同一个关键词）
    pos_hits = list(dict.fromkeys(pos_hits))
    result["positive_hits"] = pos_hits

    # 2. 负面匹配
    neg_hits = _NEG_PATTERN.findall(text_stripped)
    neg_hits = list(dict.fromkeys(neg_hits))
    result["negative_hits"] = neg_hits

    # 3. 中性偏负面
    neutral_neg_hits = _NEUTRAL_NEG_PATTERN.findall(text_stripped)
    neutral_neg_hits = list(dict.fromkeys(neutral_neg_hits))
    result["neutral_neg_hits"] = neutral_neg_hits

    # 4. 打分
    pos_score = len(pos_hits) * 3
    neg_score = len(neg_hits) * (-2)
    neutral_neg_score = len(neutral_neg_hits) * (-1)
    result["score"] = pos_score + neg_score + neutral_neg_score

    # 5. 置信度：有命中才算"有信号"，否则 0
    total_hits = len(pos_hits) + len(neg_hits) + len(neutral_neg_hits)
    result["confidence"] = min(total_hits * 0.25, 1.0) if total_hits > 0 else 0.0

    return result


def batch_score(danmus: List[str]) -> List[Dict[str, Any]]:
    """批量打分（供离线分析用）"""
    return [score_danmu(d) for d in danmus]


# ============ 反馈增强：弹幕密度检测 ============

class DanmuDensityTracker:
    """
    弹幕密度追踪器

    用于检测 AI 回复后弹幕密度是否变化（提升 = 隐式正面奖励信号）
    """

    def __init__(self, window_seconds: float = 10.0):
        self.window = window_seconds
        # 每个"窗口"的时间戳列表
        self._timestamps: List[float] = []

    def record(self, timestamp: Optional[float] = None):
        """记录一条弹幕时间戳"""
        import time
        ts = timestamp or time.time()
        self._timestamps.append(ts)

    def get_density_delta(self, response_timestamp: float) -> Dict[str, Any]:
        """
        对比 AI 回复前后窗口的弹幕密度

        Returns:
            {
                "before_count": int,
                "after_count": int,
                "ratio": float,       # after / before, 大于 2.0 表示密度翻倍
                "density_spike": bool # 是否有密度突增（正面信号）
            }
        """
        import time
        now = time.time()
        before_start = response_timestamp - self.window
        before_end = response_timestamp
        after_start = response_timestamp
        after_end = now

        before_count = sum(1 for t in self._timestamps if before_start <= t < before_end)
        after_count = sum(1 for t in self._timestamps if after_start <= t <= after_end)

        ratio = (after_count / before_count) if before_count > 0 else (
            float('inf') if after_count > 0 else 1.0
        )
        density_spike = ratio >= 2.0 and after_count >= 3

        return {
            "before_count": before_count,
            "after_count": after_count,
            "ratio": ratio,
            "density_spike": density_spike,
        }


# ============ 快速自测 ============

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    test_cases = [
        "哈哈哈好可爱啊爱了爱了",
        "666666 主播厉害",
        "这个有点尴尬",
        "无聊走了",
        "？？？",
        "好的主播",
        "唱歌好听！yyds",
        "这是啥情况",
        "笑死我了哈哈哈 主播太牛了",
        "主播声音好难听",
    ]

    print("=== 情感打分测试 ===\n")
    for text in test_cases:
        result = score_danmu(text)
        tag = "🟢" if result["score"] > 0 else ("🔴" if result["score"] < 0 else "⚪")
        print(f"{tag} '{text}'")
        print(f"   score={result['score']}, pos={result['positive_hits']}, "
              f"neg={result['negative_hits']}, neutral_neg={result['neutral_neg_hits']}, "
              f"conf={result['confidence']:.2f}\n")
