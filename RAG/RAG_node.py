#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG（检索增强生成）节点函数模块

基于 langraph 框架实现的 RAG 节点，用于执行文档检索操作，
将检索到的相关文档片段与原始查询进行整合，并返回处理后的上下文信息。
"""

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional, Dict, List, Union
import logging
import time
import uuid

from Millvus_base import (
    init_milvus_client,
    get_connection_manager,
    DoubaoEmbeddings,
    query_test_data
)

logger = logging.getLogger(__name__)

# --------- 单例与常量 ---------
# Embedding模型单例（避免每次调用重新初始化）
_global_embedding_model = None
_global_embedding_lock = None  # type: ignore[assignment]


def get_embedding_model() -> DoubaoEmbeddings:
    """获取全局单例 Embedding 模型，线程安全"""
    global _global_embedding_model, _global_embedding_lock
    if _global_embedding_lock is None:
        import threading
        _global_embedding_lock = threading.Lock()
    if _global_embedding_model is None:
        with _global_embedding_lock:
            if _global_embedding_model is None:
                _global_embedding_model = DoubaoEmbeddings()
                logger.info("[RAG] 初始化全局 Embedding 模型单例")
    return _global_embedding_model


# Milvus COSINE 距离阈值：distance = 1 - cosine_similarity，越小越相似
# distance < 0.65 表示 similarity > 0.35，可根据实际数据分布调整
MAX_DISTANCE_THRESHOLD = 0.65

# Milvus 配置常量
DEFAULT_COLLECTION = "chat_history"
DEFAULT_DB = "LLM_vtuber"
DEFAULT_TOP_K = 3
DEFAULT_METRIC = "COSINE"
DEFAULT_NPROBE = 10


class RAGState(TypedDict):
    """
    RAG 节点状态定义

    字段说明：
    - query_text: 查询文本
    - collection_name: 目标集合名称
    - db_name: 数据库名称
    - query_params: 查询参数配置
    - messages: 对话历史，包含所有消息
    - context: 检索到的上下文信息
    - retrieved_documents: 检索到的文档列表
    - output_results: 输出结果
    - response: 最新响应内容
    - error: 错误信息（如有）
    - execution_time: 执行耗时
    - retrieval_time: 检索耗时
    - num_documents: 检索到的文档数量
    - report: 生成的报告
    """
    query_text: str                      # 查询文本
    collection_name: str = "chat_history"  # 目标集合名称
    db_name: str = "LLM_vtuber"          # 数据库名称
    query_params: Optional[Dict] = None  # 查询参数配置
    messages: Optional[list[AnyMessage]] = None  # 对话历史
    context: Optional[str] = None       # 检索到的上下文信息
    retrieved_documents: Optional[List[Dict]] = None  # 检索到的文档
    output_results: Optional[Dict] = None  # 输出结果
    response: Optional[str] = None      # 最新响应
    error: Optional[str] = None         # 错误信息
    execution_time: float = 0.0         # 执行耗时
    retrieval_time: float = 0.0         # 检索耗时
    num_documents: int = 0              # 检索到的文档数量
    report: Optional[Dict] = None       # 生成的报告
    save_success: Optional[bool] = None  # 保存是否成功


class RetrievalParams(TypedDict):
    """
    检索参数定义

    字段说明：
    - top_k: 返回结果数量
    - collection_name: 集合名称
    - db_name: 数据库名称
    - metric_type: 相似度度量方式
    - nprobe: 搜索参数
    """
    top_k: int = 3
    collection_name: str = "chat_history"
    db_name: str = "LLM_vtuber"
    metric_type: str = "COSINE"
    nprobe: int = 10


# --------- 辅助函数 ---------
def _extract_last_user_content(messages: list) -> Optional[str]:
    """从消息列表中提取最后一条用户消息的内容"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content")
    return None


# 集合加载状态缓存（避免每次检索都检查加载状态）
_loaded_collections: set = set()


def _ensure_collection_loaded(client, collection_name: str):
    """
    确保 Milvus 集合已加载到内存。
    Milvus 服务重启后集合会被卸载，不加载直接搜索会报错或返回空结果。
    使用模块级缓存避免重复检查：一旦确认加载成功就不再重复检查。
    """
    if collection_name in _loaded_collections:
        return

    from pymilvus import MilvusException
    try:
        # 使用 MilvusClient 原生 API 检查加载状态
        state = client.get_load_state(collection_name=collection_name)
        # state 可能返回 "Loaded" / "Loading" / "NotExist" 等
        state_str = str(state) if state is not None else ""
        if "Load" not in state_str:
            logger.info(f"[RAG] 集合 {collection_name} 未加载(state={state_str})，正在加载...")
            client.load_collection(collection_name=collection_name)
            logger.info(f"[RAG] 集合 {collection_name} 加载完成")
        _loaded_collections.add(collection_name)
    except Exception as e:
        # 某些 pymilvus 版本不支持 get_load_state，回退到直接 load
        logger.debug(f"[RAG] 集合加载状态检查异常，尝试直接加载: {e}")
        try:
            client.load_collection(collection_name=collection_name)
            _loaded_collections.add(collection_name)
            logger.info(f"[RAG] 集合 {collection_name} 加载完成(回退)")
        except Exception as e2:
            logger.debug(f"[RAG] 集合加载跳过: {e2}")


def _milvus_search_with_retry(client, collection_name: str, vector, top_k: int,
                              metric_type: str, nprobe: int, max_retries: int = 2):
    """Milvus 搜索，带简单的瞬态错误重试"""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return client.search(
                collection_name=collection_name,
                data=[vector],
                anns_field="content_vector",
                limit=top_k,
                output_fields=["session_id", "role_id", "user_id", "content",
                                "timestamp", "message_type", "context_relevance"],
                metric_type=metric_type,
                params={"nprobe": nprobe}
            )
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                logger.warning(f"[RAG] Milvus 搜索重试 {attempt+1}/{max_retries}: {e}")
                # 尝试重连：重新加载集合
                try:
                    _ensure_collection_loaded(client, collection_name)
                except Exception:
                    pass
                time.sleep(0.5 * (attempt + 1))
            else:
                raise last_err


def _fingerprint_message(msg: dict, response_text: Optional[str] = None) -> str:
    """生成消息的指纹用于去重（避免重复保存相同消息到Milvus）"""
    role = msg.get("role", "")
    content = response_text if response_text is not None else msg.get("content", "")
    return f"{role}::{content}"


def rag_retrieval_node(state: RAGState, retrieval_params: Optional[RetrievalParams] = None) -> RAGState:
    """
    RAG 检索节点函数

    从 Milvus 中检索相关历史消息作为上下文。
    关键优化：集合加载检查、embedding单例、正确的距离阈值。
    """
    try:
        start_time = time.time()

        # 1. 合并参数
        if retrieval_params:
            params = retrieval_params
        else:
            params = {
                "top_k": state.get("query_params", {}).get("top_k", DEFAULT_TOP_K),
                "collection_name": state.get("collection_name", DEFAULT_COLLECTION),
                "db_name": state.get("db_name", DEFAULT_DB),
                "metric_type": state.get("query_params", {}).get("metric_type", DEFAULT_METRIC),
                "nprobe": state.get("query_params", {}).get("nprobe", DEFAULT_NPROBE)
            }

        # 2. 获取查询文本
        query = state.get("query_text") or _extract_last_user_content(state.get("messages", []))
        if not query:
            raise ValueError("未找到查询文本")

        logger.info(f"[RAG] 检索: 查询='{query[:50]}...', 参数={params}")

        # 3. 获取 Milvus 客户端并确保集合已加载（Milvus重启后必须手动加载）
        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=params["db_name"]
        )

        col_name = params["collection_name"]
        _ensure_collection_loaded(client, col_name)

        # 4. 生成查询向量（使用全局单例）
        embedding_model = get_embedding_model()
        query_vector = embedding_model.embed_query(query)

        # 5. 执行检索（带重试）
        results = _milvus_search_with_retry(
            client=client,
            collection_name=col_name,
            vector=query_vector,
            top_k=params["top_k"],
            metric_type=params["metric_type"],
            nprobe=params["nprobe"]
        )

        # 6. 处理结果：注意 COSINE metric 中 distance = 1 - cosine_similarity
        retrieved_docs = []
        context_parts = []

        for hits in results:
            for hit in hits:
                distance = float(hit.get("distance", 1.0))  # 越小越好

                if distance > MAX_DISTANCE_THRESHOLD:
                    logger.debug(f"[RAG] 跳过距离过大文档: {distance:.4f} > {MAX_DISTANCE_THRESHOLD}")
                    continue

                similarity = round(1.0 - distance, 4)  # 真正的余弦相似度

                doc = {
                    "session_id": hit.get("session_id"),
                    "role_id": hit.get("role_id"),
                    "user_id": hit.get("user_id"),
                    "content": hit.get("content"),
                    "timestamp": hit.get("timestamp"),
                    "message_type": hit.get("message_type"),
                    "context_relevance": hit.get("context_relevance", 0.0),
                    "distance": distance,
                    "similarity": similarity
                }
                retrieved_docs.append(doc)
                context_parts.append(f"[文档] {doc['content']} (相似度: {similarity:.4f})")

        context = "\n".join(context_parts) if context_parts else "无相关文档"

        retrieval_time = time.time() - start_time

        output_results = {
            "query": query,
            "retrieved_count": len(retrieved_docs),
            "documents": retrieved_docs,
            "summary": f"成功检索到 {len(retrieved_docs)} 条与'{query}'相关的文档",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }

        report = {
            "input": {
                "query_text": query,
                "collection_name": col_name,
                "db_name": params["db_name"],
                "query_params": params
            },
            "results": output_results,
            "error": None,
            "execution_time": retrieval_time,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }

        logger.info(f"[RAG] 检索完成: 找到 {len(retrieved_docs)} 个文档, 耗时 {retrieval_time:.2f}s")

        return RAGState(
            query_text=query,
            collection_name=col_name,
            db_name=params["db_name"],
            query_params=params,
            messages=state.get("messages"),
            context=context,
            retrieved_documents=retrieved_docs,
            output_results=output_results,
            response=state.get("response"),
            error=None,
            execution_time=retrieval_time,
            retrieval_time=retrieval_time,
            num_documents=len(retrieved_docs),
            report=report
        )

    except Exception as e:
        error_msg = f"检索失败: {str(e)}"
        logger.error(f"[RAG] {error_msg}")

        report = {
            "input": {
                "query_text": state.get("query_text", ""),
                "collection_name": state.get("collection_name", DEFAULT_COLLECTION),
                "db_name": state.get("db_name", DEFAULT_DB),
                "query_params": state.get("query_params")
            },
            "results": None,
            "error": error_msg,
            "execution_time": time.time() - start_time if 'start_time' in locals() else 0,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }

        return RAGState(
            query_text=state.get("query_text", ""),
            collection_name=state.get("collection_name", DEFAULT_COLLECTION),
            db_name=state.get("db_name", DEFAULT_DB),
            query_params=state.get("query_params"),
            messages=state.get("messages"),
            context=None,
            retrieved_documents=None,
            output_results=None,
            response=state.get("response"),
            error=error_msg,
            execution_time=0.0,
            retrieval_time=0.0,
            num_documents=0,
            report=report
        )


def rag_save_node(state: RAGState) -> RAGState:
    """
    RAG 保存节点函数

    将当前轮用户消息和AI回复保存到 Milvus。
    关键优化：
    1. 使用全局 embedding 单例（避免每次新建）
    2. 去重：如果用户消息在更早轮次已保存，跳过（基于消息指纹）
    3. 只保存「最后一条用户消息 + 本伦助手回复」，避免历史消息重复插入
    """
    try:
        start_time = time.time()

        collection_name = state.get("collection_name", DEFAULT_COLLECTION)
        db_name = state.get("db_name", DEFAULT_DB)

        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=db_name
        )
        _ensure_collection_loaded(client, collection_name)

        embedding_model = get_embedding_model()

        messages = state.get("messages", [])
        response_content = state.get("response")

        # 只保存最后一条用户消息（老的历史消息之前已经保存过）
        insert_data: List[dict] = []
        inserted_fingerprints: set = set()

        # 1. 最后一条用户消息
        last_user_msg = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        if last_user_msg:
            content = last_user_msg.get("content", "").strip()
            if content:
                fp = _fingerprint_message(last_user_msg)
                if fp not in inserted_fingerprints:
                    inserted_fingerprints.add(fp)
                    vector = embedding_model.embed_query(content)
                    insert_data.append({
                        "content": content,
                        "role_id": "user",
                        "user_id": last_user_msg.get("name", "unknown_user"),
                        "username": last_user_msg.get("name", "未知用户"),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
                        "message_type": "user_message",
                        "content_vector": vector,
                        "session_id": "default_session",
                        "context_relevance": 0.0,
                        "is_important": False,
                        "metadata": {}
                    })

        # 2. 本轮助手回复
        if response_content:
            fp = _fingerprint_message({"role": "assistant"}, response_content)
            if fp not in inserted_fingerprints:
                vector = embedding_model.embed_query(response_content)
                insert_data.append({
                    "content": response_content,
                    "role_id": "assistant",
                    "user_id": "assistant",
                    "username": "AI助手",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
                    "message_type": "assistant_message",
                    "content_vector": vector,
                    "session_id": "default_session",
                    "context_relevance": 0.0,
                    "is_important": False,
                    "metadata": {}
                })

        if insert_data:
            client.insert(collection_name=collection_name, data=insert_data)
            logger.info(f"[RAG] 成功保存 {len(insert_data)} 条消息到 Milvus (去重后)")

        execution_time = time.time() - start_time

        return RAGState(
            query_text=state.get("query_text", ""),
            collection_name=collection_name,
            db_name=db_name,
            query_params=state.get("query_params"),
            messages=messages,
            context=state.get("context"),
            retrieved_documents=state.get("retrieved_documents"),
            output_results=state.get("output_results"),
            response=response_content,
            error=None,
            execution_time=execution_time,
            retrieval_time=state.get("retrieval_time", 0.0),
            num_documents=state.get("num_documents", 0),
            report=state.get("report"),
            save_success=True
        )

    except Exception as e:
        error_msg = f"保存消息失败: {str(e)}"
        logger.error(f"[RAG] {error_msg}")

        return RAGState(
            query_text=state.get("query_text", ""),
            collection_name=state.get("collection_name", DEFAULT_COLLECTION),
            db_name=state.get("db_name", DEFAULT_DB),
            query_params=state.get("query_params"),
            messages=state.get("messages"),
            context=state.get("context"),
            retrieved_documents=state.get("retrieved_documents"),
            output_results=state.get("output_results"),
            response=state.get("response"),
            error=error_msg,
            execution_time=0.0,
            retrieval_time=state.get("retrieval_time", 0.0),
            num_documents=state.get("num_documents", 0),
            report=state.get("report"),
            save_success=False
        )


def create_rag_context(retrieved_docs: List[Dict], query: str) -> str:
    """
    创建 RAG 上下文
    """
    if not retrieved_docs:
        return f"用户查询: {query}\n\n无相关文档"

    context_parts = [f"用户查询: {query}", "\n=== 相关文档 ==="]

    for i, doc in enumerate(retrieved_docs, 1):
        content = doc.get("content", "")
        similarity = doc.get("similarity", 0.0)
        context_parts.append(f"\n[{i}] 相似度: {similarity:.4f}\n{content}")

    return "\n".join(context_parts)


def create_initial_rag_state() -> RAGState:
    """
    创建初始 RAG 状态
    """
    return RAGState(
        query_text="",
        collection_name="chat_history",
        db_name="LLM_vtuber",
        query_params={
            "top_k": 3,
            "metric_type": "COSINE",
            "nprobe": 10
        },
        messages=None,
        context=None,
        retrieved_documents=None,
        output_results=None,
        response=None,
        error=None,
        execution_time=0.0,
        retrieval_time=0.0,
        num_documents=0,
        report=None,
        save_success=None
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_state = {
        "query_text": "Milvus支持哪些向量索引类型？",
        "collection_name": "chat_history",
        "db_name": "LLM_vtuber",
        "query_params": {
            "top_k": 3,
            "metric_type": "COSINE",
            "nprobe": 10
        },
        "messages": [
            {"role": "user", "content": "你好，我想了解一下Milvus"},
            {"role": "assistant", "content": "Milvus是一个向量数据库"},
            {"role": "user", "content": "Milvus支持哪些向量索引类型？"}
        ]
    }

    print("=== 测试 RAG 检索节点 ===")
    print(f"用户查询: {test_state['query_text']}")

    result = rag_retrieval_node(test_state)

    print(f"\n检索耗时: {result['retrieval_time']:.2f} 秒")
    print(f"执行耗时: {result['execution_time']:.2f} 秒")
    print(f"检索到的文档数量: {result['num_documents']}")
    print(f"\n上下文信息:")
    print(result['context'])

    if result['error']:
        print(f"\n错误: {result['error']}")
    else:
        print("\n✅ 检索成功！")
        print(f"\n输出结果:")
        print(f"  检索到的文档数: {result['output_results']['retrieved_count']}")
        print(f"  摘要: {result['output_results']['summary']}")