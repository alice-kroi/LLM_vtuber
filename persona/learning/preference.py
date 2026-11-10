# ============================================================
# persona/learning/preference.py — 偏好规则抽取（零 LLM）
# ============================================================
# 只认明确句式，不猜。
#   称呼：以后/就/可以叫我 X；别叫我 / 不要叫我 X
#   话题：我喜欢 / 我最喜欢 / 我讨厌 / 我不喜欢 X
#   风格：说话简短/详细一点
#   雷点：不要提 / 别再提 X
#
# 单条消息最多产出 3 条偏好（防止一次长文把记忆灌满）。
# ============================================================

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

MAX_HITS = 3
MAX_VALUE_LEN = 20

# 抽取结果没有意义的泛称／代词
_STOP_VALUES = {
    "你", "你们", "您", "他", "她", "它", "我", "我们", "这个", "那个",
    "这些", "那些", "这", "那", "一个", "什么", "啥", "都", "了", "吗",
    "的", "吧", "啊", "呀", "哦", "呢", "啦", "嘛", "哈", "一下", "一点",
}
# "叫我起床/过来" 这类是动词短语，不是称呼
_CALL_VERBS = {
    "起床", "过来", "过去", "去", "走", "停", "醒醒", "看看", "来", "回去",
    "滚", "闭嘴", "安静", "别动",
}
# 语气词／标点：从取值尾部剥掉
_TAIL_CHARS = "啊呀哦呢吧啦嘛哈的了吧～~，。！？、,.!?;；:：\"'“”‘’ "
_LEAD_CHARS = "，。！？、,.!?;；:：\"'“”‘’ "

_NEG_Q = ("什么", "啥", "哪", "怎么", "为什么", "吗", "？", "?")


@dataclass(frozen=True)
class PreferenceHit:
    slot: str       # 称呼 / 话题 / 风格 / 雷点
    value: str
    polarity: int   # +1 喜欢 / -1 反感


# (正则, 槽位, 极性)；顺序即优先级
_RULES: List[Tuple[re.Pattern, str, int]] = [
    # ---- 称呼（否定式必须先于肯定式，否则"别叫我老板"会被当成喜欢）----
    (re.compile(r"(?:别|不要|不准|不许)(?:再)?叫我(?P<v>[^，。,.!?；;]{1,12})"), "称呼", -1),
    (re.compile(r"(?:以后|就|可以|请)(?:再)?叫我(?P<v>[^，。,.!?；;]{1,12})"), "称呼", 1),
    # "喜欢被叫老板" / "喜欢叫我小鲤"；不匹配"喜欢叫外卖"
    (re.compile(r"(?:喜欢|希望|想)(?:(?:被|别人)(?:叫|称呼)|(?:叫|称呼)我)"
                r"(?P<v>[^，。,.!?；;]{1,12})"), "称呼", 1),
    # ---- 话题 ----
    (re.compile(r"我(?:最|很|超|特别|挺|比较|真的)?(?:喜欢|爱)(?P<v>[^，。,.!?；;？?]{1,24})"), "话题", 1),
    (re.compile(r"我(?:最|很|超|特别|挺|比较|真的)?(?:讨厌|不喜欢|反感|受不了|烦)"
                r"(?P<v>[^，。,.!?；;？?]{1,24})"), "话题", -1),
    # ---- 风格 ----
    (re.compile(r"(?:说话|回复|回答|聊天)[^，。,.!?]{0,4}?(?:简短|简洁|短)(?:一点|点|些)?"), "风格", 1),
    (re.compile(r"(?:说话|回复|回答|聊天)[^，。,.!?]{0,4}?(?:详细|具体)(?:一点|点|些)?"), "风格", 1),
    # ---- 雷点 ----
    (re.compile(r"(?:不要|别)(?:再)?(?:提|说)(?P<v>[^，。,.!?；;]{1,20})"), "雷点", -1),
]


def _clean(raw: str) -> str:
    value = (raw or "").strip().strip(_LEAD_CHARS)
    # 只保留第一个短句
    value = re.split(r"[，。,.!?；;~～]", value)[0]
    value = value.strip().strip(_TAIL_CHARS)
    return value.strip()


def _valid(value: str, slot: str) -> bool:
    if not value or len(value) > MAX_VALUE_LEN:
        return False
    if value in _STOP_VALUES:
        return False
    if any(q in value for q in _NEG_Q):
        return False
    if slot == "称呼" and value in _CALL_VERBS:
        return False
    # "喜欢被叫老板"是称呼，不该同时记成话题
    if slot == "话题" and value.startswith(("被叫", "叫")):
        return False
    return True


def extract(text: str) -> List[PreferenceHit]:
    """从一句话里抽取偏好；抽不出就返回空列表。"""
    if not text or not isinstance(text, str):
        return []
    body = text.strip()
    if not body:
        return []

    hits: List[PreferenceHit] = []
    seen = set()

    for pattern, slot, polarity in _RULES:
        match = pattern.search(body)
        if not match:
            continue
        if slot == "风格":
            value = _style_value(match.group(0))
        else:
            value = _clean(match.group("v"))
        if not _valid(value, slot):
            continue
        key = (slot, value)
        if key in seen:
            continue
        seen.add(key)
        hits.append(PreferenceHit(slot=slot, value=value, polarity=polarity))
        if len(hits) >= MAX_HITS:
            break
    return hits


def _style_value(sentence: str) -> str:
    """风格句子里提取取值（简短 / 详细）"""
    if "简短" in sentence or "简洁" in sentence or "短" in sentence:
        return "简短"
    if "详细" in sentence or "具体" in sentence:
        return "详细"
    return ""


__all__ = ["PreferenceHit", "extract"]
