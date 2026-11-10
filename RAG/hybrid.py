"""混合检索基础组件：分词、BM25、RRF 融合。

不依赖第三方分词库：
- 英文按字母数字切词并小写，去停用词
- 中文按相邻二字组（bigram）切分，去停用词/停用字组合
- BM25 与 RRF 只作用于候选池，用于重排 dense 结果
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence

_ASCII_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_BLOCK_RE = re.compile(r"[\u4e00-\u9fff]+")

_EN_STOPWORDS = frozenset(
    "a an the of to in on at for and or nor but if then than that this these those "
    "is are was were be been being do does did done have has had "
    "what which who whom whose how why when where "
    "i you he she it we they me him her them my your our their "
    "not no can could may might must shall should will would".split()
)

_ZH_STOP_WORDS = frozenset(
    "什么 怎么 如何 为何 哪些 哪个 请问 可以 能够 需要 是否 一个 一些 "
    "我们 你们 他们 这个 那个 这样 那样 就是 还是 所以 因为 但是".split())

_ZH_STOP_CHARS = frozenset(
    "的 了 是 在 和 与 及 或 也 都 就 而 被 把 让 给 对 从 到 为 以 于 之 其 "
    "这 那 有 无 我 你 他 她 它 们 吗 呢 吧 啊 呀 嘛".split())


def tokenize(text: str) -> List[str]:
    """英文小写单词 + 中文二字组，用于 BM25。"""
    text = (text or "").lower()
    tokens = [
        word for word in _ASCII_RE.findall(text)
        if len(word) > 1 and word not in _EN_STOPWORDS
    ]
    for block in _CJK_BLOCK_RE.findall(text):
        if len(block) == 1:
            if block not in _ZH_STOP_CHARS:
                tokens.append(block)
            continue
        for i in range(len(block) - 1):
            bigram = block[i:i + 2]
            if bigram in _ZH_STOP_WORDS:
                continue
            if bigram[0] in _ZH_STOP_CHARS or bigram[1] in _ZH_STOP_CHARS:
                continue
            tokens.append(bigram)
    return tokens


def lexical_terms(query: str, max_terms: int = 10) -> List[str]:
    """生成 Milvus `like` 过滤用的词面。

    英文保留原始大小写并附带小写变体（正文里 RLHF / llm 可能大小写混用），
    中文使用二字组。长词更具体，优先保留。
    """
    terms: List[str] = []
    seen = set()
    for word in _ASCII_RE.findall(query or ""):
        if len(word) < 2 or word.lower() in _EN_STOPWORDS:
            continue
        for variant in (word, word.lower()):
            if variant not in seen:
                seen.add(variant)
                terms.append(variant)
    for block in _CJK_BLOCK_RE.findall(query or ""):
        for i in range(len(block) - 1):
            bigram = block[i:i + 2]
            if bigram in seen or bigram in _ZH_STOP_WORDS:
                continue
            if bigram[0] in _ZH_STOP_CHARS or bigram[1] in _ZH_STOP_CHARS:
                continue
            seen.add(bigram)
            terms.append(bigram)
    terms.sort(key=len, reverse=True)
    return terms[:max(1, int(max_terms))]


def l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if not norm:
        return [0.0] * len(vector)
    return [float(v) / norm for v in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """两个向量的余弦相似度；长度不一致返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


class BM25:
    """候选池内的 BM25（k1/b 可调），语料统计只来自候选块。"""

    def __init__(self, docs: Sequence[Sequence[str]],
                 k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        self._tf = [Counter(doc) for doc in docs]
        self._len = [len(doc) for doc in docs]
        self._n = len(docs)
        self._avgdl = (sum(self._len) / self._n) if self._n else 0.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {
            term: math.log(1 + (self._n - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }

    def scores(self, query_tokens: Sequence[str]) -> List[float]:
        result = [0.0] * self._n
        if not self._n:
            return result
        avgdl = self._avgdl or 1.0
        for term in set(query_tokens):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self._tf):
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self._k1 * (
                    1 - self._b + self._b * self._len[i] / avgdl)
                result[i] += idf * freq * (self._k1 + 1) / denom
        return result


def rrf_fuse(rankings: Iterable[Sequence[str]], k: int = 60,
             weights: Optional[Sequence[float]] = None) -> Dict[str, float]:
    """加权 Reciprocal Rank Fusion：把多路排名融成一个分数，越大越靠前。

    weights 对应每条排名的权重；词法通常应低于向量，避免词面命中把
    语义上不相关的块顶到最前。
    """
    weight_list = list(weights or [])
    fused: Dict[str, float] = {}
    for i, ranking in enumerate(rankings):
        weight = weight_list[i] if i < len(weight_list) else 1.0
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + weight / (k + rank)
    return fused
