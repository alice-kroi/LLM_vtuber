#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆节点模块

用于实现记忆节点功能，将大语言模型生成的回答持久化存储到 Milvus 数据库中。

核心功能：
- 连接 Milvus 数据库
- 处理大模型回答的持久化存储
- 确保数据存储操作的原子性和可靠性
- 处理异常情况
- 提供清晰的接口供其他模块调用
"""
import sys
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional, Dict, List, Union, Any
import logging
import time
import uuid
import os

# 导入 Milvus 相关模块
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from RAG.Millvus_base import init_milvus_client, get_connection_manager, DoubaoEmbeddings

logger = logging.getLogger(__name__)


class MemoryState(TypedDict):
    """
    记忆节点状态定义
    
    字段说明：
    - messages: 对话历史，包含所有消息
    - query: 当前用户查询
    - response: 大模型生成的回答
    - session_id: 会话ID
    - user_id: 用户ID
    - metadata: 相关元数据
    - error: 错误信息（如有）
    - storage_time: 存储耗时
    - storage_success: 存储是否成功
    """
    messages: list[AnyMessage]      # 对话历史
    query: str                      # 当前用户查询
    response: str                   # 大模型生成的回答
    session_id: str                 # 会话ID
    user_id: str                    # 用户ID
    metadata: Optional[Dict[str, Any]] = None  # 相关元数据
    error: Optional[str] = None     # 错误信息
    storage_time: float = 0.0       # 存储耗时
    storage_success: bool = False   # 存储是否成功


class MemoryNode:
    """
    记忆节点类，用于处理大模型回答的持久化存储
    """

    # 嵌入向量维度（与 Milvus 集合定义一致）
    VECTOR_DIM = 2560

    def __init__(self, **kwargs):
        """
        初始化记忆节点（使用连接复用）

        Args:
            **kwargs: 配置参数
                - uri: Milvus 服务地址
                - token: Milvus 访问令牌
                - db_name: 数据库名称
                - collection_name: 集合名称
                - embedding_model: 嵌入模型实例
        """
        self.uri = kwargs.get("uri", "http://localhost:19530")
        self.token = kwargs.get("token", "root:Milvus")
        self.db_name = kwargs.get("db_name", "LLM_vtuber")
        self.collection_name = kwargs.get("collection_name", "chat_history")
        self.embedding_model = kwargs.get("embedding_model", DoubaoEmbeddings())
        self.manager = get_connection_manager(
            uri=self.uri,
            token=self.token,
            db_name=self.db_name
        )
        self.client = None
        
    def connect(self):
        """
        连接到 Milvus 数据库（使用连接复用）
        
        Returns:
            bool: 连接是否成功
        """
        try:
            self.client = self.manager.get_client()
            return True
        except Exception as e:
            logger.error(f"获取 Milvus 连接失败: {str(e)}")
            return False
    
    def disconnect(self):
        """
        断开与 Milvus 数据库的连接
        """
        if self.client:
            try:
                self.client.close()
                self.client = None
                logger.info("成功断开与 Milvus 数据库的连接")
            except Exception as e:
                logger.error(f"断开连接失败: {str(e)}")
    
    def store_response(self, state: MemoryState) -> MemoryState:
        """
        存储大模型生成的回答到数据库
        
        Args:
            state: 记忆节点状态，包含需要存储的信息
        
        Returns:
            更新后的记忆节点状态，包含存储结果
        """
        start_time = time.time()
        
        try:
            # 确保客户端已连接
            if not self.client:
                if not self.connect():
                    return MemoryState(
                        **state,
                        error="数据库连接失败",
                        storage_time=time.time() - start_time,
                        storage_success=False
                    )
            
            # 提取存储所需的信息
            session_id = state.get("session_id", str(uuid.uuid4()))
            user_id = state.get("user_id", str(uuid.uuid4()))
            query = state.get("query", "")
            response = state.get("response", "")
            metadata = state.get("metadata", {})
            
            # 验证必填字段
            if not response:
                return MemoryState(
                    **state,
                    error="回答内容为空，无法存储",
                    storage_time=time.time() - start_time,
                    storage_success=False
                )
            
            # 为回答生成嵌入向量
            response_vector = self.embedding_model.embed_query(response)
            
            # 检查并调整向量维度
            if len(response_vector) != self.VECTOR_DIM:
                if len(response_vector) >= self.VECTOR_DIM:
                    response_vector = response_vector[:self.VECTOR_DIM]
                else:
                    response_vector = response_vector + [0.0] * (self.VECTOR_DIM - len(response_vector))
            
            # 格式化时间戳
            timestamp_str = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            
            # 准备存储数据
            store_data = [
                {
                    "session_id": session_id,
                    "role_id": "assistant",  # 大模型角色
                    "user_id": user_id,
                    "message_type": "assistant_message",
                    "content": response,
                    "content_vector": response_vector,
                    "timestamp": timestamp_str,
                    "context_relevance": 1.0,  # 假设回答与查询完全相关
                    "is_important": True,  # 大模型回答通常比较重要
                    "metadata": {
                        "query": query,
                        "source": "memory_node",
                        "timestamp": timestamp_str,
                        **metadata
                    }
                }
            ]
            
            # 存储数据到 Milvus
            result = self.client.insert(
                collection_name=self.collection_name,
                data=store_data
            )
            
            # 计算存储耗时
            storage_time = time.time() - start_time
            
            logger.info(f"成功存储大模型回答，插入结果: {result}")
            
            # 创建新的状态字典，避免重复设置 error 参数
            new_state = {**state}
            new_state['error'] = None
            new_state['storage_time'] = storage_time
            new_state['storage_success'] = True
            
            return MemoryState(**new_state)
            
        except Exception as e:
            error_msg = f"存储大模型回答失败: {str(e)}"
            logger.error(error_msg)
            
            # 创建新的状态字典，避免重复设置 error 参数
            new_state = {**state}
            new_state['error'] = error_msg
            new_state['storage_time'] = time.time() - start_time
            new_state['storage_success'] = False
            
            return MemoryState(**new_state)


_memory_node_instance = None

def _get_memory_node_instance() -> MemoryNode:
    """获取全局MemoryNode实例（连接复用）"""
    global _memory_node_instance
    if _memory_node_instance is None:
        _memory_node_instance = MemoryNode()
    return _memory_node_instance

def memory_node(state: MemoryState) -> MemoryState:
    """
    记忆节点函数，用于将大模型生成的回答持久化存储（使用连接复用）
    
    Args:
        state: 记忆节点状态，包含需要存储的信息
    
    Returns:
        更新后的记忆节点状态，包含存储结果
    """
    # 获取全局MemoryNode实例（连接复用）
    memory_node_instance = _get_memory_node_instance()
    
    try:
        # 执行存储操作
        result = memory_node_instance.store_response(state)
        
        return result
        
    except Exception as e:
        error_msg = f"记忆节点执行失败: {str(e)}"
        logger.error(error_msg)
        
        # 创建新的状态字典，避免重复设置 error 参数
        new_state = {**state}
        new_state['error'] = error_msg
        new_state['storage_time'] = 0.0
        new_state['storage_success'] = False
        
        return MemoryState(**new_state)


def main():
    """
    主函数，用于测试记忆节点功能
    """
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 测试状态
    test_state = MemoryState(
        messages=[
            {"role": "user", "content": "什么是机器学习？"},
            {"role": "assistant", "content": "机器学习是人工智能的一个分支，它使计算机能够从数据中学习而不需要被明确编程。"}
        ],
        query="什么是机器学习？",
        response="机器学习是人工智能的一个分支，它使计算机能够从数据中学习而不需要被明确编程。",
        session_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        metadata={"model": "doubao-seed-1-8-251228", "temperature": 0.7}
    )
    
    print("=== 测试记忆节点功能 ===")
    print(f"用户查询: {test_state['query']}")
    print(f"大模型回答: {test_state['response']}")
    
    # 执行存储
    result = memory_node(test_state)
    
    # 打印结果
    print(f"\n存储耗时: {result['storage_time']:.2f} 秒")
    print(f"存储成功: {result['storage_success']}")
    
    if result['error']:
        print(f"错误: {result['error']}")
    else:
        print("✅ 大模型回答存储成功！")


if __name__ == "__main__":
    main()
