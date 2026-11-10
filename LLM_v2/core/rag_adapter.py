# ============================================================
# LLM_v2/core/rag_adapter.py — RAG 适配器（V2 简化版）
# ============================================================
# 封装 RAG/RAG_node.py 的核心检索功能，提供 async 接口
# 不依赖 langgraph state（V2 用 dict）
# 借鉴 Generative Agents：三因子评分（recency + relevance + importance）
# ============================================================

from __future__ import annotations

import asyncio
import configparser
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 初始化失败后的熔断退避：30s → 60s → 120s → 240s → 300s 封顶
_INIT_BASE_COOLDOWN = 30.0
_INIT_MAX_COOLDOWN = 300.0

# RAG/ 目录不在 sys.path，需要加
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RAG_DIR = _PROJECT_ROOT / "RAG"
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))


# 单例
_RAG_INSTANCE: Optional['RAGAdapter'] = None


class RAGAdapter:
    """RAG 适配器：异步封装 Milvus 检索

    提供：
        - retrieve(query, top_k) -> List[检索结果]
        - score_results(results) -> 加权排序
    """

    def __init__(self, top_k: int = 3):
        self.top_k = top_k
        self._client = None
        self._embedding = None
        self._collection = "chat_history"
        self._db = "LLM_vtuber"
        self._uri: Optional[str] = None
        self._token: Optional[str] = None
        self._initialized = False
        # 初始化熔断状态（避免 Milvus 不可用时每条消息都重连等 10 秒）
        self._init_lock = threading.Lock()
        self._init_failures = 0
        self._next_init_at = 0.0
        self._init_error = ""

    def _ensure_init(self) -> bool:
        """懒加载 Milvus 客户端 + Embedding；失败后指数退避熔断。

        Returns:
            True 表示已初始化可用；False 表示当前不可用（含熔断冷却中）。
        """
        if self._initialized:
            return True

        now = time.time()
        if now < self._next_init_at:
            return False

        # 非阻塞锁：已有线程在尝试初始化时，其他请求立即跳过，不排队
        if not self._init_lock.acquire(blocking=False):
            return False

        try:
            if self._initialized:
                return True

            # 优先环境变量 > config.ini
            self._uri = os.getenv("MILVUS_URI", "http://localhost:19530")
            self._token = os.getenv("MILVUS_TOKEN", "")

            config_path = Path("config.ini")
            if config_path.exists():
                cfg = configparser.ConfigParser()
                cfg.read(config_path, encoding='utf-8')
                if cfg.has_section("milvus"):
                    self._uri = self._uri or cfg.get("milvus", "uri", fallback=self._uri)
                    self._token = self._token or cfg.get("milvus", "token", fallback=self._token)
                    self._db = cfg.get("milvus", "database", fallback=self._db)
                    self._collection = cfg.get("milvus", "collection", fallback=self._collection)

            # 延迟 import（避免循环依赖）
            from RAG.Millvus_base import init_milvus_client
            from RAG.RAG_node import get_embedding_model
            self._client = init_milvus_client(
                uri=self._uri,
                token=self._token,
                db_name=self._db,
            )
            self._embedding = get_embedding_model()
            self._initialized = True
            self._init_failures = 0
            self._next_init_at = 0.0
            self._init_error = ""
            logger.info(f"[rag] initialized: uri={self._uri} db={self._db}")
            return True
        except Exception as e:
            self._client = None
            self._embedding = None
            self._initialized = False
            self._init_failures += 1
            cooldown = min(
                _INIT_MAX_COOLDOWN,
                _INIT_BASE_COOLDOWN * (2 ** (self._init_failures - 1)),
            )
            self._next_init_at = time.time() + cooldown
            self._init_error = str(e)[:200]
            logger.warning(
                f"[rag] init failed (RAG 不可用，熔断 {cooldown:.0f}s，"
                f"连续失败 {self._init_failures}): {e}"
            )
            return False
        finally:
            self._init_lock.release()

    def status(self) -> Dict[str, Any]:
        """RAG 可用性快照（供日志/WebUI 诊断）"""
        return {
            "initialized": self._initialized,
            "failures": self._init_failures,
            "error": self._init_error,
            "retry_in_sec": round(max(0.0, self._next_init_at - time.time()), 1),
        }

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """异步检索相关历史消息

        Args:
            query: 查询文本
            top_k: 返回数量（默认 self.top_k）
            collection: 集合名（默认 self._collection）

        Returns:
            [{"content": str, "score": float, "session_id": str, ...}, ...]
        """
        if not query:
            return []

        # 熔断冷却中：直接返回，不进入线程池、不重连 Milvus
        if not self._initialized and time.time() < self._next_init_at:
            return []

        # 在线程池跑同步检索（Milvus SDK 是同步的）
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._retrieve_sync, query, top_k or self.top_k, collection or self._collection
        )

    def _retrieve_sync(
        self, query: str, top_k: int, collection: str,
    ) -> List[Dict[str, Any]]:
        """同步检索（在线程池跑）"""
        if not self._ensure_init() or self._client is None:
            return []

        try:
            # 1. 嵌入
            query_vector = self._embedding.embed_query(query)

            # 2. Milvus 检索
            from RAG.RAG_node import _ensure_collection_loaded
            _ensure_collection_loaded(self._client, collection)
            results = self._client.search(
                collection_name=collection,
                data=[query_vector],
                anns_field="content_vector",
                limit=top_k,
                output_fields=["session_id", "role_id", "user_id", "content",
                              "timestamp", "message_type", "context_relevance"],
                metric_type="COSINE",
                params={"nprobe": 10},
            )

            # 3. 解析 + 三因子评分
            hits = []
            for hit in results[0]:
                entity = hit.get("entity", {}) or {}
                distance = hit.get("distance", 1.0)
                # COSINE 指标下 distance 即相似度（越大越相似）
                relevance = max(0.0, min(1.0, float(distance)))
                ts = entity.get("timestamp", 0)
                # recency: 1 / (1 + days_since)
                if ts:
                    try:
                        ts_float = float(ts) if not isinstance(ts, (int, float)) else ts
                        days_ago = max(0, (time.time() - ts_float) / 86400)
                        recency = 1.0 / (1.0 + days_ago)
                    except (ValueError, TypeError):
                        recency = 0.5
                else:
                    recency = 0.5
                importance = float(entity.get("context_relevance", 0.5))
                # Generative Agents 三因子加权
                score = 0.3 * recency + 0.5 * relevance + 0.2 * importance
                hits.append({
                    "content": entity.get("content", ""),
                    "score": score,
                    "relevance": relevance,
                    "recency": recency,
                    "importance": importance,
                    "session_id": entity.get("session_id", ""),
                    "user_id": entity.get("user_id", ""),
                    "role_id": entity.get("role_id", ""),
                    "timestamp": ts,
                    "message_type": entity.get("message_type", ""),
                })

            hits.sort(key=lambda x: -x["score"])
            return hits

        except Exception as e:
            logger.warning(f"[rag] 检索失败: {e}")
            return []

    async def build_context(self, query: str, top_k: int = 3) -> str:
        """检索历史对话并拼装上下文。

        知识库资料归思维脑（`LLM_v2.core.will_knowledge`），本适配器只负责
        `chat_history`，避免两个脑同时持有知识检索入口。
        """
        if not query:
            return ""

        try:
            history = await self.retrieve(query, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rag] 历史检索失败: {e}")
            history = []

        if not history:
            return ""

        lines: List[str] = ["【历史相关对话】"]
        for i, r in enumerate(history, 1):
            content = r["content"]
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(
                f"{i}. [{r['message_type']}] {content} "
                f"(score={r['score']:.2f})")
        return "\n".join(lines)


def get_rag(top_k: int = 3) -> RAGAdapter:
    """获取 RAG 适配器单例"""
    global _RAG_INSTANCE
    if _RAG_INSTANCE is None:
        _RAG_INSTANCE = RAGAdapter(top_k=top_k)
    return _RAG_INSTANCE


__all__ = ['RAGAdapter', 'get_rag']
