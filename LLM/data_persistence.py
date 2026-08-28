#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据持久化模块

负责将聊天内容存储到 Milvus 数据库中，确保数据的完整性和一致性。
"""

import logging
from typing import Dict, Any, List
import sys
import os

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RAG"))
from Millvus_base import init_milvus_client, get_connection_manager

logger = logging.getLogger(__name__)

class DataPersistence:
    """
    数据持久化类（使用连接复用）
    """
    
    def __init__(self, uri: str = "", token: str = "", db_name: str = ""):
        """
        初始化数据持久化对象
        
        Args:
            uri: Milvus 服务地址（默认从环境变量 MILVUS_URI 读取）
            token: 认证令牌（默认从环境变量 MILVUS_TOKEN 读取）
            db_name: 数据库名称（默认从环境变量 MILVUS_DB 读取）
        """
        self.uri = uri or os.getenv("MILVUS_URI", "http://localhost:19530")
        self.token = token or os.getenv("MILVUS_TOKEN", "")
        self.db_name = db_name or os.getenv("MILVUS_DB", "LLM")
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
            logger.error(f"连接 Milvus 数据库失败: {e}")
            return False
    
    def disconnect(self):
        """
        断开与 Milvus 数据库的连接
        """
        if self.client:
            try:
                self.client.close()
                self.client = None
                logger.info("已关闭 Milvus 客户端连接")
            except Exception as e:
                logger.error(f"关闭 Milvus 客户端连接时发生错误: {e}")
    
    def store_message(self, message: Dict[str, Any]) -> bool:
        """
        存储单条消息到数据库
        
        Args:
            message: 消息数据
        
        Returns:
            bool: 存储是否成功
        """
        try:
            if not self.client:
                if not self.connect():
                    return False
            
            # 准备插入数据
            insert_data = [
                {
                    "message_id": message.get("message_id"),
                    "role": message.get("role"),
                    "content": message.get("content"),
                    "timestamp": message.get("timestamp"),
                    "status": message.get("status"),
                    "vector": message.get("vector") if message.get("vector") is not None else [0.0] * 1536
                }
            ]
            
            # 插入数据
            result = self.client.insert(
                collection_name="messages",
                data=insert_data
            )
            
            logger.info(f"成功存储消息: {message.get('message_id')}")
            return True
        except Exception as e:
            logger.error(f"存储消息失败: {e}")
            return False
    
    def store_messages(self, messages: List[Dict[str, Any]]) -> bool:
        """
        批量存储消息到数据库
        
        Args:
            messages: 消息数据列表
        
        Returns:
            bool: 存储是否成功
        """
        try:
            if not self.client:
                if not self.connect():
                    return False
            
            # 准备插入数据
            insert_data = []
            for message in messages:
                insert_data.append({
                    "message_id": message.get("message_id"),
                    "role": message.get("role"),
                    "content": message.get("content"),
                    "timestamp": message.get("timestamp"),
                    "status": message.get("status"),
                    "vector": message.get("vector") if message.get("vector") is not None else [0.0] * 1536
                })
            
            # 插入数据
            result = self.client.insert(
                collection_name="messages",
                data=insert_data
            )
            
            logger.info(f"成功存储 {len(messages)} 条消息")
            return True
        except Exception as e:
            logger.error(f"批量存储消息失败: {e}")
            return False
    
    def get_message(self, message_id: str) -> Dict[str, Any]:
        """
        根据消息 ID 获取消息
        
        Args:
            message_id: 消息 ID
        
        Returns:
            消息数据或 None
        """
        try:
            if not self.client:
                if not self.connect():
                    return None
            
            # 查询数据
            result = self.client.query(
                collection_name="messages",
                filter=f"message_id == '{message_id}'",
                output_fields=["message_id", "role", "content", "timestamp", "status", "vector"]
            )
            
            if result:
                logger.info(f"成功获取消息: {message_id}")
                return result[0]
            else:
                logger.warning(f"消息不存在: {message_id}")
                return None
        except Exception as e:
            logger.error(f"获取消息失败: {e}")
            return None
    
    def get_messages_by_role(self, role: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        根据角色获取消息
        
        Args:
            role: 角色
            limit: 限制数量
        
        Returns:
            消息数据列表
        """
        try:
            if not self.client:
                if not self.connect():
                    return []
            
            # 查询数据
            result = self.client.query(
                collection_name="messages",
                filter=f"role == '{role}'",
                output_fields=["message_id", "role", "content", "timestamp", "status", "vector"],
                limit=limit
            )
            
            logger.info(f"成功获取 {len(result)} 条 {role} 消息")
            return result
        except Exception as e:
            logger.error(f"获取消息失败: {e}")
            return []
    
    def count_messages(self) -> int:
        """
        统计消息数量
        
        Returns:
            消息数量
        """
        try:
            if not self.client:
                if not self.connect():
                    return 0
            
            # 使用查询方法获取消息数量
            result = self.client.query(
                collection_name="messages",
                filter="",
                output_fields=["message_id"],
                limit=10000
            )
            
            count = len(result)
            logger.info(f"消息总数: {count}")
            return count
        except Exception as e:
            logger.error(f"统计消息数量失败: {e}")
            return 0


# 全局数据持久化实例
data_persistence = DataPersistence()


def get_data_persistence() -> DataPersistence:
    """
    获取数据持久化实例
    
    Returns:
        数据持久化实例
    """
    return data_persistence
