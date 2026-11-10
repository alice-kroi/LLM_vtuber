#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 语义缓存（Semantic Cache）

对语义相似的用户弹幕命中缓存，直接复用之前 LLM 生成过的回复，
避免重复调用大模型 —— 显著降低高弹幕场景的延迟与 token 成本。

核心：用豆包 embedding 计算查询向量，与缓存问题向量做余弦相似度，
相似度 ≥ 阈值 且 未过期 → 命中，直接返回缓存答案。

设计原则：
- 单例 + 线程锁（异步事件循环内安全）
- 带 TTL 与容量上限，避免无限膨胀
- 由调用方决定哪些意图可缓存（默认 skip/normal 简单互动）
"""

import logging
import math
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None


class SemanticCache:
    """语义缓存器：基于向量余弦相似度的回复复用"""

    _instance: Optional["SemanticCache"] = None

    def __init__(
        self,
        threshold: float = 0.85,
        ttl_seconds: float = 1800.0,
        max_items: int = 200,
        enabled: bool = True,
    ):
        self.threshold = threshold
        self.ttl = ttl_seconds
        self.max_items = max_items
        self.enabled = enabled
        self._items: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._embed = None  # 惰性绑定豆包 embedding 函数
        self._hits = 0
        self._misses = 0

    @classmethod
    def get_instance(cls, **kw) -> "SemanticCache":
        if cls._instance is None:
            cls._instance = cls(**kw)
        return cls._instance

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        logger.info(f"[语义缓存] {'启用' if enabled else '关闭'}")

    # --------- embedding ---------
    def _get_embed(self):
        """惰性获取豆包 embedding 函数（直接复用 DoubaoEmbeddings，避开 RAG_node 重依赖）"""
        if self._embed is None:
            from RAG.Millvus_base import DoubaoEmbeddings
            self._embed = DoubaoEmbeddings().embed_query
        return self._embed

    def _embed_query(self, text: str) -> Optional[list]:
        try:
            return self._get_embed()(text)
        except Exception as e:
            logger.debug(f"[语义缓存] embedding 失败: {e}")
            return None

    # --------- P0-B：异步版本（embedding 放线程池，不阻塞事件循环） ---------
    async def get_async(self, question: str) -> Tuple[Optional[str], bool]:
        """异步查询缓存：embedding 计算在线程池执行，避免阻塞 asyncio 事件循环。"""
        if not self.enabled or not question:
            return None, False
        import asyncio
        qvec = await asyncio.to_thread(self._embed_query, question)
        if not qvec:
            return None, False
        now = time.time()
        best_sim = 0.0
        best_resp = None
        with self._lock:
            for it in list(self._items):
                if now - it["ts"] > self.ttl:
                    self._items.remove(it)
                    continue
                sim = self._cosine(qvec, it["vec"])
                if sim > best_sim:
                    best_sim = sim
                    best_resp = it["resp"]
            if best_sim >= self.threshold and best_resp:
                self._hits += 1
                return best_resp, True
            self._misses += 1
            return None, False

    async def put_async(self, question: str, response: str):
        """异步写入缓存：embedding 计算在线程池执行。"""
        if not self.enabled or not question or not response:
            return
        import asyncio
        qvec = await asyncio.to_thread(self._embed_query, question)
        if not qvec:
            return
        with self._lock:
            if len(self._items) >= self.max_items:
                self._items = self._items[-self.max_items // 2:]
            self._items.append({
                "q": question[:200],
                "vec": qvec,
                "resp": response,
                "ts": time.time(),
            })

    @staticmethod
    def _cosine(a, b):
        if np is not None:
            av = np.asarray(a, dtype=float)
            bv = np.asarray(b, dtype=float)
            denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
            if denom == 0:
                return 0.0
            return float(np.dot(av, bv) / denom)
        # 纯 python 兜底
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na * nb == 0:
            return 0.0
        return dot / (na * nb)

    # --------- 主接口 ---------
    def get(self, question: str) -> Tuple[Optional[str], bool]:
        """
        查询缓存：相似问题命中则返回缓存回复。

        Returns:
            (回复或 None, 是否命中)
        """
        if not self.enabled or not question:
            return None, False
        qvec = self._embed_query(question)
        if not qvec:
            return None, False

        now = time.time()
        best_sim = 0.0
        best_resp = None
        with self._lock:
            for it in list(self._items):
                if now - it["ts"] > self.ttl:
                    self._items.remove(it)  # 清理过期
                    continue
                sim = self._cosine(qvec, it["vec"])
                if sim > best_sim:
                    best_sim = sim
                    best_resp = it["resp"]
            if best_sim >= self.threshold and best_resp:
                self._hits += 1
                return best_resp, True
            self._misses += 1
            return None, False

    def put(self, question: str, response: str):
        """将问题-回复对存入缓存"""
        if not self.enabled or not question or not response:
            return
        qvec = self._embed_query(question)
        if not qvec:
            return
        with self._lock:
            # 容量上限：淘汰最旧的
            if len(self._items) >= self.max_items:
                self._items = self._items[-self.max_items // 2:]
            self._items.append({
                "q": question[:200],
                "vec": qvec,
                "resp": response,
                "ts": time.time(),
            })

    def clear(self):
        with self._lock:
            self._items.clear()

    def stats(self) -> Dict[str, Any]:
        return {
            "items": len(self._items),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / (self._hits + self._misses), 3)
            if (self._hits + self._misses) else 0.0,
        }


# 全局便捷函数
_cache_instance: Optional[SemanticCache] = None


def get_semantic_cache(**kw) -> SemanticCache:
    """获取全局语义缓存实例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache.get_instance(**kw)
    return _cache_instance