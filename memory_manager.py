#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory 管理模块

基于 LangGraph InMemoryStore 实现的双层记忆系统：
1. 短期记忆（InMemoryStore）：存储当前会话的对话历史，供 LLM 上下文使用
2. 长期记忆（Milvus）：存储持久化知识，供 RAG 检索使用

流程：
- 回答前：从 Milvus 加载用户历史记忆到 InMemoryStore
- 回答时：InMemoryStore 中的对话历史作为 LLM 上下文
- 回答后：将对话更新到 InMemoryStore，新知识保存到 Milvus
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from langgraph.store.memory import InMemoryStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """基于 InMemoryStore 的记忆管理器"""

    def __init__(self):
        self.store = InMemoryStore()
        self._initialized = False

    async def initialize(self):
        """初始化（预留接口，当前 InMemoryStore 无需特殊初始化）"""
        if not self._initialized:
            self._initialized = True
            logger.info("MemoryManager 初始化完成")

    # ---------- 短期记忆操作 ----------

    async def load_user_memory(self, user_id: str) -> List[Dict[str, Any]]:
        """
        加载用户的短期记忆（对话历史）
        """
        try:
            items = await self.store.asearch(
                ('user_memory', user_id),
                query='',
                limit=50
            )
            messages = []
            for item in items:
                if item.value.get('type') == 'message':
                    messages.append(item.value)
            logger.info(f"加载用户 {user_id} 的短期记忆: {len(messages)} 条消息 (store_id={id(self.store)})")
            if messages:
                logger.debug(f"  记忆内容: {[m.get('content', '')[:50] for m in messages]}")
            return messages
        except Exception as e:
            logger.warning(f"加载用户记忆失败: {e}")
            return []

    async def save_message(self, user_id: str, message: Dict[str, Any]):
        """
        保存一条消息到短期记忆
        """
        try:
            key = f"msg_{int(time.time() * 1000)}"
            value = {
                'type': 'message',
                'role': message.get('role', 'unknown'),
                'content': message.get('content', ''),
                'name': message.get('name', ''),
                'timestamp': time.time()
            }
            await self.store.aput(('user_memory', user_id), key, value)
            logger.info(f"保存消息到用户 {user_id}: {key} (store_id={id(self.store)}, content={value['content'][:30]})")
            
            # 立即验证保存是否成功
            verify = await self.store.aget(('user_memory', user_id), key)
            if verify:
                logger.info(f"  验证保存成功: {verify.value.get('content', '')[:30]}")
            else:
                logger.error(f"  验证保存失败!")
        except Exception as e:
            logger.warning(f"保存消息失败: {e}")

    async def save_context(self, user_id: str, context_data: Dict[str, Any]):
        """
        保存上下文信息到短期记忆

        Args:
            user_id: 用户 ID
            context_data: 上下文数据
        """
        try:
            key = f"ctx_{int(time.time() * 1000)}"
            value = {
                'type': 'context',
                **context_data,
                'timestamp': time.time()
            }
            await self.store.aput(('user_memory', user_id), key, value)
            logger.debug(f"保存上下文到用户 {user_id} 的记忆: {key}")
        except Exception as e:
            logger.warning(f"保存上下文失败: {e}")

    async def get_relevant_memory(self, user_id: str, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取与查询相关的记忆

        Args:
            user_id: 用户 ID
            query: 查询文本（用于语义搜索）
            limit: 返回条数

        Returns:
            相关记忆列表
        """
        try:
            items = await self.store.asearch(
                ('user_memory', user_id),
                query=query,
                limit=limit
            )
            return [item.value for item in items]
        except Exception as e:
            logger.warning(f"获取相关记忆失败: {e}")
            return []

    async def clear_user_memory(self, user_id: str):
        """
        清除用户的所有短期记忆

        Args:
            user_id: 用户 ID
        """
        try:
            namespaces = await self.store.alist_namespaces(prefix=('user_memory', user_id))
            for ns in namespaces:
                logger.info(f"清除命名空间: {ns}")
            logger.info(f"用户 {user_id} 的短期记忆已清除")
        except Exception as e:
            logger.warning(f"清除用户记忆失败: {e}")

    async def get_memory_stats(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户记忆统计

        Args:
            user_id: 用户 ID

        Returns:
            统计信息
        """
        try:
            items = await self.store.asearch(('user_memory', user_id), query='', limit=1000)
            messages = [i for i in items if i.value.get('type') == 'message']
            contexts = [i for i in items if i.value.get('type') == 'context']
            return {
                'total_items': len(items),
                'message_count': len(messages),
                'context_count': len(contexts)
            }
        except Exception as e:
            return {'error': str(e)}


# ---------- 全局单例 ----------

_memory_manager_instance: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 实例"""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemoryManager()
    return _memory_manager_instance
