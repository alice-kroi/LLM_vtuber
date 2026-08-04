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


def rag_retrieval_node(state: RAGState, retrieval_params: Optional[RetrievalParams] = None) -> RAGState:
    """
    RAG 检索节点函数

    接收用户查询作为输入，执行文档检索操作，将检索到的相关文档片段与原始查询进行整合，
    并返回处理后的上下文信息以支持后续的生成任务。
    """
    try:
        start_time = time.time()

        if retrieval_params:
            params = retrieval_params
        else:
            params = {
                "top_k": state.get("query_params", {}).get("top_k", 3),
                "collection_name": state.get("collection_name", "chat_history"),
                "db_name": state.get("db_name", "LLM_vtuber"),
                "metric_type": state.get("query_params", {}).get("metric_type", "COSINE"),
                "nprobe": state.get("query_params", {}).get("nprobe", 10)
            }

        query = state.get("query_text")
        if not query:
            for msg in reversed(state.get("messages", [])):
                if msg.get("role") == "user":
                    query = msg.get("content")
                    break
            if not query:
                raise ValueError("未找到查询文本")

        logger.info(f"开始执行检索: 查询='{query[:50]}...', 参数={params}")

        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=params["db_name"]
        )

        embedding_model = DoubaoEmbeddings()

        results = client.search(
            collection_name=params["collection_name"],
            data=[embedding_model.embed_query(query)],
            anns_field="content_vector",
            limit=params["top_k"],
            output_fields=["session_id", "role_id", "user_id", "content", "timestamp", "message_type", "context_relevance"],
            metric_type=params["metric_type"],
            params={"nprobe": params["nprobe"]}
        )

        retrieved_docs = []
        context_parts = []
        
        MIN_SIMILARITY_THRESHOLD = 0.9

        for hits in results:
            for hit in hits:
                similarity = hit.get("distance", 0.0)
                
                if similarity < MIN_SIMILARITY_THRESHOLD:
                    logger.debug(f"跳过低相似度文档: {similarity:.4f} < {MIN_SIMILARITY_THRESHOLD}")
                    continue
                    
                doc = {
                    "session_id": hit.get("session_id"),
                    "role_id": hit.get("role_id"),
                    "user_id": hit.get("user_id"),
                    "content": hit.get("content"),
                    "timestamp": hit.get("timestamp"),
                    "message_type": hit.get("message_type"),
                    "context_relevance": hit.get("context_relevance", 0.0),
                    "similarity": similarity
                }
                retrieved_docs.append(doc)
                context_parts.append(f"[文档] {doc['content']} (相似度: {doc['similarity']:.4f})")

        context = "\n".join(context_parts) if context_parts else "无相关文档"

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
                "collection_name": params["collection_name"],
                "db_name": params["db_name"],
                "query_params": params
            },
            "results": output_results,
            "error": None,
            "execution_time": time.time() - start_time,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }

        retrieval_time = time.time() - start_time
        execution_time = retrieval_time

        logger.info(f"检索完成: 找到 {len(retrieved_docs)} 个文档, 耗时 {retrieval_time:.2f} 秒")

        return RAGState(
            query_text=query,
            collection_name=params["collection_name"],
            db_name=params["db_name"],
            query_params=params,
            messages=state.get("messages"),
            context=context,
            retrieved_documents=retrieved_docs,
            output_results=output_results,
            response=state.get("response"),
            error=None,
            execution_time=execution_time,
            retrieval_time=retrieval_time,
            num_documents=len(retrieved_docs),
            report=report
        )

    except Exception as e:
        error_msg = f"检索失败: {str(e)}"
        logger.error(error_msg)

        params = {
            "top_k": state.get("query_params", {}).get("top_k", 3),
            "collection_name": state.get("collection_name", "chat_history"),
            "db_name": state.get("db_name", "LLM_vtuber"),
            "metric_type": state.get("query_params", {}).get("metric_type", "COSINE"),
            "nprobe": state.get("query_params", {}).get("nprobe", 10)
        }

        report = {
            "input": {
                "query_text": state.get("query_text", ""),
                "collection_name": params["collection_name"],
                "db_name": params["db_name"],
                "query_params": params
            },
            "results": None,
            "error": error_msg,
            "execution_time": time.time() - (state.get("execution_time", 0) + state.get("retrieval_time", 0)),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }

        return RAGState(
            query_text=state.get("query_text", ""),
            collection_name=params["collection_name"],
            db_name=params["db_name"],
            query_params=params,
            messages=state.get("messages"),
            context=None,
            retrieved_documents=None,
            output_results=None,
            response=state.get("response"),
            error=error_msg,
            execution_time=time.time() - (state.get("execution_time", 0) + state.get("retrieval_time", 0)),
            retrieval_time=0.0,
            num_documents=0,
            report=report
        )


def rag_save_node(state: RAGState) -> RAGState:
    """
    RAG 保存节点函数

    将对话消息保存到 Milvus 数据库中。
    """
    try:
        start_time = time.time()

        collection_name = state.get("collection_name", "chat_history")
        db_name = state.get("db_name", "LLM_vtuber")

        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=db_name
        )

        embedding_model = DoubaoEmbeddings()

        messages = state.get("messages", [])
        response_content = state.get("response")

        insert_data = []

        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    vector = embedding_model.embed_query(content)

                    insert_data.append({
                        "content": content,
                        "role_id": "user",
                        "user_id": msg.get("name", "unknown_user"),
                        "username": msg.get("name", "未知用户"),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
                        "message_type": "user_message",
                        "content_vector": vector,
                        "session_id": "default_session",
                        "context_relevance": 0.0,
                        "is_important": False,
                        "metadata": {}
                    })

        if response_content:
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
            client.insert(
                collection_name=collection_name,
                data=insert_data
            )
            logger.info(f"成功保存 {len(insert_data)} 条消息到 Milvus")
            print(f"成功保存 {len(insert_data)} 条消息到 Milvus")

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
        logger.error(error_msg)
        print(f"保存消息失败: {e}")

        return RAGState(
            query_text=state.get("query_text", ""),
            collection_name=state.get("collection_name", "chat_history"),
            db_name=state.get("db_name", "LLM_vtuber"),
            query_params=state.get("query_params"),
            messages=state.get("messages"),
            context=state.get("context"),
            retrieved_documents=state.get("retrieved_documents"),
            output_results=state.get("output_results"),
            response=state.get("response"),
            error=error_msg,
            execution_time=time.time(),
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