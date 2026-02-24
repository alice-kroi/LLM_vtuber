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

from .Millvus_base import (
    init_milvus_client,
    DoubaoEmbeddings,
    query_test_data
)

logger = logging.getLogger(__name__)


class RAGState(TypedDict):
    """
    RAG 节点状态定义
    
    字段说明：
    - messages: 对话历史，包含所有消息
    - query: 当前用户查询
    - context: 检索到的上下文信息
    - retrieved_documents: 检索到的文档列表
    - response: 最新响应内容
    - error: 错误信息（如有）
    - retrieval_time: 检索耗时
    - num_documents: 检索到的文档数量
    """
    messages: list[AnyMessage]      # 对话历史
    query: str                      # 当前用户查询
    context: Optional[str] = None   # 检索到的上下文信息
    retrieved_documents: Optional[List[Dict]] = None  # 检索到的文档
    response: Optional[str] = None  # 最新响应
    error: Optional[str] = None     # 错误信息
    retrieval_time: float = 0.0     # 检索耗时
    num_documents: int = 0          # 检索到的文档数量


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
    
    Args:
        state: RAG 状态对象，包含对话历史和当前查询
        retrieval_params: 检索参数，包含 top_k、collection_name 等配置
    
    Returns:
        更新后的 RAG 状态，包含检索结果和上下文信息
    
    Examples:
        >>> from RAG.RAG_node import rag_retrieval_node, RAGState
        >>> state = {
        ...     "messages": [{"role": "user", "content": "Milvus是什么？"}],
        ...     "query": "Milvus是什么？"
        ... }
        >>> result = rag_retrieval_node(state)
        >>> print(f"检索到 {result['num_documents']} 个文档")
        >>> print(f"上下文信息: {result['context']}")
    """
    try:
        # 记录开始时间
        start_time = time.time()
        
        # 使用默认检索参数或用户提供的参数
        if retrieval_params:
            params = retrieval_params
        else:
            # 创建带有默认值的参数字典
            params = {
                "top_k": 3,
                "collection_name": "chat_history",
                "db_name": "LLM_vtuber",
                "metric_type": "COSINE",
                "nprobe": 10
            }
        
        # 提取当前查询
        query = state.get("query")
        if not query:
            # 尝试从消息历史中提取最后一条用户消息
            for msg in reversed(state.get("messages", [])):
                if msg.get("role") == "user":
                    query = msg.get("content")
                    break
            if not query:
                raise ValueError("未找到查询文本")
        
        logger.info(f"开始执行检索: 查询='{query[:50]}...', 参数={params}")
        
        # 初始化 Milvus 客户端
        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=params["db_name"]
        )
        
        # 初始化嵌入模型
        embedding_model = DoubaoEmbeddings()
        
        # 执行向量搜索
        results = client.search(
            collection_name=params["collection_name"],
            data=[embedding_model.embed_query(query)],  # 搜索数据
            anns_field="content_vector",  # 向量字段名
            limit=params["top_k"],  # 返回结果数量
            output_fields=["message_id", "session_id", "role_id", "user_id", "content", "timestamp", "message_type"],  # 返回的字段
            metric_type=params["metric_type"],  # 相似度度量方式
            params={"nprobe": params["nprobe"]}  # 搜索参数
        )
        
        # 处理检索结果
        retrieved_docs = []
        context_parts = []
        
        for hits in results:
            for hit in hits:
                doc = {
                    "message_id": hit.get("message_id"),
                    "content": hit.get("content"),
                    "username": hit.get("username"),
                    "timestamp": hit.get("timestamp"),
                    "similarity": hit.get("distance", 0.0)
                }
                retrieved_docs.append(doc)
                context_parts.append(f"[文档] {doc['content']} (相似度: {doc['similarity']:.4f})")
        
        # 构建上下文信息
        context = "\n".join(context_parts) if context_parts else "无相关文档"
        
        # 计算检索耗时
        retrieval_time = time.time() - start_time
        
        logger.info(f"检索完成: 找到 {len(retrieved_docs)} 个文档, 耗时 {retrieval_time:.2f} 秒")
        
        # 更新状态并返回
        return RAGState(
            messages=state["messages"],
            query=query,
            context=context,
            retrieved_documents=retrieved_docs,
            response=state.get("response"),
            error=None,
            retrieval_time=retrieval_time,
            num_documents=len(retrieved_docs)
        )
        
    except Exception as e:
        # 处理错误并返回错误状态
        error_msg = f"检索失败: {str(e)}"
        logger.error(error_msg)
        
        return RAGState(
            messages=state["messages"],
            query=state.get("query", ""),
            context=None,
            retrieved_documents=None,
            response=state.get("response"),
            error=error_msg,
            retrieval_time=0.0,
            num_documents=0
        )


def create_rag_context(retrieved_docs: List[Dict], query: str) -> str:
    """
    创建 RAG 上下文
    
    将检索到的文档与原始查询整合成上下文信息，用于后续的生成任务。
    
    Args:
        retrieved_docs: 检索到的文档列表
        query: 用户查询
    
    Returns:
        str: 整合后的上下文信息
    """
    if not retrieved_docs:
        return f"用户查询: {query}\n\n无相关文档"
    
    context_parts = [f"用户查询: {query}", "\n=== 相关文档 ==="]
    
    for i, doc in enumerate(retrieved_docs, 1):
        content = doc.get("content", "")
        similarity = doc.get("similarity", 0.0)
        context_parts.append(f"\n[{i}] 相似度: {similarity:.4f}\n{content}")
    
    return "\n".join(context_parts)


if __name__ == "__main__":
    """
    测试 RAG 节点函数
    """
    # 配置日志
    logging.basicConfig(level=logging.INFO)
    
    # 测试状态
    test_state = {
        "messages": [
            {"role": "user", "content": "你好，我想了解一下Milvus"},
            {"role": "assistant", "content": "Milvus是一个向量数据库"},
            {"role": "user", "content": "Milvus支持哪些向量索引类型？"}
        ],
        "query": "Milvus支持哪些向量索引类型？"
    }
    
    # 测试检索参数
    test_params = {
        "top_k": 3,
        "collection_name": "chat_history",
        "db_name": "LLM_vtuber"
    }
    
    print("=== 测试 RAG 检索节点 ===")
    print(f"用户查询: {test_state['query']}")
    
    # 执行检索
    result = rag_retrieval_node(test_state, test_params)
    
    # 打印结果
    print(f"\n检索耗时: {result['retrieval_time']:.2f} 秒")
    print(f"检索到的文档数量: {result['num_documents']}")
    print(f"\n上下文信息:")
    print(result['context'])
    
    if result['error']:
        print(f"\n错误: {result['error']}")
    else:
        print("\n✅ 检索成功！")
