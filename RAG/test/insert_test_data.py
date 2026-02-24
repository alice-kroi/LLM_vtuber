#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试数据生成和插入程序

用于给 LLM_vtuber 项目的 Milvus 数据库生成和插入测试数据，
包含各种类型的对话和知识数据，以支持 RAG 检索功能。
"""

from pymilvus import MilvusClient
from langchain_core.embeddings import Embeddings
import logging
import uuid
import time
import json
import os

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from RAG.Millvus_base import (
    init_milvus_client,
    DoubaoEmbeddings
)

logger = logging.getLogger(__name__)


def generate_test_data():
    """
    生成测试数据
    
    Returns:
        list: 测试数据列表
    """
    print("=== 生成测试数据 ===")
    
    # 生成会话ID
    session_id = str(uuid.uuid4())
    
    # 生成各类测试数据
    test_data = [
        # 机器学习相关知识
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "机器学习是人工智能的一个分支，它使计算机能够从数据中学习而不需要被明确编程。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "监督学习是机器学习的一种方法，它使用标记好的数据来训练模型。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "无监督学习是机器学习的一种方法，它使用未标记的数据来发现数据中的模式。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "深度学习是机器学习的一个分支，它使用多层神经网络来模拟人脑的学习过程。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "神经网络是由多个神经元组成的计算模型，它能够学习复杂的模式。",
            "message_type": "knowledge"
        },
        # 自然语言处理相关知识
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "自然语言处理是人工智能的一个分支，它使计算机能够理解和处理人类语言。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "词嵌入是将单词转换为向量表示的技术，它能够捕捉单词之间的语义关系。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "Transformer是一种基于注意力机制的神经网络架构，它在自然语言处理任务中取得了显著的成果。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "BERT是一种预训练的语言模型，它能够理解上下文信息，在各种NLP任务中表现出色。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "GPT是一种生成式预训练Transformer模型，它能够生成连贯的自然语言文本。",
            "message_type": "knowledge"
        },
        # 向量数据库相关知识
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "向量数据库是一种专门用于存储和检索向量数据的数据库，它在AI应用中非常重要。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "Milvus是一个开源的向量数据库，它支持高效的向量相似度搜索。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "向量索引是提高向量搜索效率的关键技术，常见的索引类型包括HNSW、IVF等。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "相似度度量是向量搜索的核心，常见的度量方法包括余弦相似度、欧氏距离等。",
            "message_type": "knowledge"
        },
        {
            "session_id": session_id,
            "role_id": "system",
            "user_id": str(uuid.uuid4()),
            "content": "RAG（检索增强生成）是一种结合了检索和生成的AI技术，它能够利用外部知识来提高生成质量。",
            "message_type": "knowledge"
        },
        # 聊天对话示例
        {
            "session_id": session_id,
            "role_id": "user",
            "user_id": str(uuid.uuid4()),
            "content": "你好，我想了解一下机器学习。",
            "message_type": "user_message"
        },
        {
            "session_id": session_id,
            "role_id": "assistant",
            "user_id": str(uuid.uuid4()),
            "content": "机器学习是人工智能的一个分支，它使计算机能够从数据中学习而不需要被明确编程。",
            "message_type": "assistant_message"
        },
        {
            "session_id": session_id,
            "role_id": "user",
            "user_id": str(uuid.uuid4()),
            "content": "机器学习有哪些主要类型？",
            "message_type": "user_message"
        },
        {
            "session_id": session_id,
            "role_id": "assistant",
            "user_id": str(uuid.uuid4()),
            "content": "机器学习主要分为监督学习、无监督学习和强化学习三大类。",
            "message_type": "assistant_message"
        },
        {
            "session_id": session_id,
            "role_id": "user",
            "user_id": str(uuid.uuid4()),
            "content": "什么是向量数据库？",
            "message_type": "user_message"
        },
        {
            "session_id": session_id,
            "role_id": "assistant",
            "user_id": str(uuid.uuid4()),
            "content": "向量数据库是一种专门用于存储和检索向量数据的数据库，它在AI应用中非常重要。",
            "message_type": "assistant_message"
        }
    ]
    
    print(f"生成了 {len(test_data)} 条测试数据")
    return test_data


def insert_test_data_to_milvus():
    """
    插入测试数据到 Milvus 数据库
    
    Returns:
        bool: 插入是否成功
    """
    try:
        # 初始化参数
        uri = "http://localhost:19530"
        token = "root:Milvus"
        db_name = "LLM_vtuber"
        collection_name = "chat_history"
        
        print("=== 连接到 Milvus 数据库 ===")
        
        # 初始化 Milvus 客户端
        client = init_milvus_client(uri=uri, token=token, db_name=db_name)
        
        # 生成测试数据
        test_data = generate_test_data()
        
        # 初始化嵌入模型
        embedding_model = DoubaoEmbeddings()
        
        # 生成嵌入向量
        print("=== 生成嵌入向量 ===")
        contents = [msg["content"] for msg in test_data]
        vectors = embedding_model.embed_documents(contents)
        print(f"生成的向量维度: {len(vectors[0])}")
        
        # 检查并调整向量维度
        if len(vectors[0]) != 2560:
            print("=== 调整向量维度 ===")
            print(f"当前维度: {len(vectors[0])}, 目标维度: 2560")
            # 这里可以添加维度调整逻辑
            # 暂时使用一个简单的方法：取前2560维
            adjusted_vectors = []
            for vec in vectors:
                if len(vec) >= 2560:
                    adjusted_vectors.append(vec[:2560])
                else:
                    # 如果向量太短，用0填充
                    adjusted_vectors.append(vec + [0.0] * (2560 - len(vec)))
            vectors = adjusted_vectors
            print(f"调整后的向量维度: {len(vectors[0])}")
        
        # 准备插入数据
        insert_data = []
        for i, msg in enumerate(test_data):
            # 格式化时间戳为 TIMESTAMPTZ 格式
            timestamp_str = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            insert_data.append({
                "session_id": msg["session_id"],
                "role_id": msg["role_id"],
                "user_id": msg["user_id"],
                "message_type": msg["message_type"],
                "content": msg["content"],
                "content_vector": vectors[i],
                "timestamp": timestamp_str,
                "context_relevance": 0.0,  # 默认相关性得分
                "is_important": False,     # 默认不重要
                "metadata": {              # 默认元数据
                    "source": "test_data",
                    "version": "1.0"
                }
            })
        
        # 插入数据
        print("\n=== 插入数据到 Milvus ===")
        result = client.insert(
            collection_name=collection_name,
            data=insert_data
        )
        
        print(f"成功插入 {len(insert_data)} 条数据")
        print(f"插入结果: {result}")
        
        # 统计消息数量
        print("\n=== 统计消息数量 ===")
        try:
            # 加载集合
            client.load_collection(collection_name=collection_name)
            
            results = client.query(
                collection_name=collection_name,
                filter="message_id IS NOT NULL",
                output_fields=["message_id"],
                limit=1000
            )
            
            message_count = len(results)
            print(f"总消息数量: {message_count}")
        except Exception as e:
            print(f"统计消息数量失败: {str(e)}")
            print("注意：数据插入已成功，统计失败不影响数据可用性")
        
        # 关闭客户端
        client.close()
        
        return True
        
    except Exception as e:
        print(f"插入测试数据失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """
    主函数
    """
    print("=== LLM_vtuber 测试数据插入程序 ===")
    print("\n此程序用于向 Milvus 数据库插入测试数据，以支持 RAG 检索功能。")
    print("\n步骤:")
    print("1. 连接到 Milvus 数据库")
    print("2. 生成测试数据")
    print("3. 为数据生成嵌入向量")
    print("4. 插入数据到数据库")
    print("5. 验证插入结果")
    
    # 执行插入
    success = insert_test_data_to_milvus()
    
    if success:
        print("\n✅ 测试数据插入成功！")
        print("\n现在你可以运行 RAG 节点来测试检索功能了。")
    else:
        print("\n❌ 测试数据插入失败，请检查错误信息。")


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 运行主函数
    main()
