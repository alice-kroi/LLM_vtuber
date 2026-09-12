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
import os
import sqlite3
import time
import json
from typing import Any, Dict, List, Optional

from langgraph.store.memory import InMemoryStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """基于 InMemoryStore 的记忆管理器（含 SQLite 持久层）"""

    def __init__(self):
        self.store = InMemoryStore()
        self._initialized = False
        # SQLite 持久层：用于长期累计奖励/知识，跨进程重启保留自主学习成果
        self._db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "learning_data.db"
        )
        self._db_conn = None
        self._init_sqlite()

    def _init_sqlite(self):
        """初始化 SQLite 表（带锁保护，多线程安全）"""
        try:
            self._db_conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._db_conn.row_factory = sqlite3.Row
            self._db_conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    store_type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._db_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_type "
                "ON learning_records(session_id, store_type, created_at)"
            )
            self._db_conn.commit()
            logger.debug(f"MemoryManager SQLite 持久层就绪: {self._db_path}")
        except Exception as e:
            logger.warning(f"MemoryManager SQLite 初始化失败（降级为纯内存）: {e}")
            self._db_conn = None

    def _db_write(self, session_id: str, store_type: str, value: dict):
        """写入 SQLite（尽力而为，失败不影响主流程）"""
        if self._db_conn is None:
            return
        try:
            self._db_conn.execute(
                "INSERT INTO learning_records(session_id, store_type, value, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, store_type, json.dumps(value, ensure_ascii=False), time.time()),
            )
            self._db_conn.commit()
        except Exception as e:
            logger.warning(f"SQLite 写入失败: {e}")

    def _db_query(
        self,
        session_id: str,
        store_type: Optional[str],
        since_ts: Optional[float],
        until_ts: Optional[float],
        limit: int,
    ) -> List[dict]:
        """从 SQLite 查询（尽力而为）"""
        if self._db_conn is None:
            return []
        try:
            clauses = ["session_id = ?"]
            params: list = [session_id]
            if store_type:
                clauses.append("store_type = ?")
                params.append(store_type)
            if since_ts is not None:
                clauses.append("created_at >= ?")
                params.append(since_ts)
            if until_ts is not None:
                clauses.append("created_at <= ?")
                params.append(until_ts)
            params.append(limit)
            sql = (
                "SELECT value FROM learning_records "
                f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?"
            )
            rows = self._db_conn.execute(sql, params).fetchall()
            return [json.loads(row["value"]) for row in rows]
        except Exception as e:
            logger.warning(f"SQLite 查询失败: {e}")
            return []

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

    # ---------- 奖励存储（reward_store） ----------
    # namespace: ('reward_store', session_id)
    # 存两类数据:
    #   - reward_event: 单条奖励事件（弹幕/礼物/SC）
    #   - response_reward: 聚合后的回复奖励

    async def save_reward_event(self, event: Dict[str, Any], session_id: str = "default"):
        """
        保存一条奖励事件（单条弹幕情感/礼物/SC）

        Args:
            event: 由 RewardEvent.create() 生成的 dict
            session_id: 会话标识（默认 "default"）
        """
        try:
            key = f"re_{int(time.time() * 1000)}_{id(event) % 1000}"
            value = {
                "store_type": "reward_event",
                **event,
            }
            await self.store.aput(('reward_store', session_id), key, value)
            # 同步写盘，跨重启保留
            self._db_write(session_id, "reward_event", value)
        except Exception as e:
            logger.warning(f"保存奖励事件失败: {e}")

    async def save_reward_aggregation(self, agg: Dict[str, Any], session_id: str = "default"):
        """
        保存一条聚合后的回复奖励

        Args:
            agg: 由 ResponseRewardAggregator.finalize() 返回的 dict
            session_id: 会话标识
        """
        try:
            key = f"ra_{int(time.time() * 1000)}_{id(agg) % 1000}"
            value = {
                "store_type": "response_reward",
                **agg,
            }
            await self.store.aput(('reward_store', session_id), key, value)
            # 同步写盘，跨重启保留
            self._db_write(session_id, "response_reward", value)
        except Exception as e:
            logger.warning(f"保存奖励聚合失败: {e}")

    async def get_reward_history(
        self,
        session_id: str = "default",
        since_ts: Optional[float] = None,
        until_ts: Optional[float] = None,
        limit: int = 200,
        store_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        查询奖励历史

        Args:
            session_id: 会话标识
            since_ts: 起始时间戳（可选）
            until_ts: 结束时间戳（可选）
            limit: 最多返回条数
            store_type: 过滤类型 "reward_event" / "response_reward"（可选）

        Returns:
            奖励记录列表
        """
        try:
            items = await self.store.asearch(
                ('reward_store', session_id), query='', limit=limit
            )
            results = []
            for item in items:
                v = item.value
                if store_type and v.get("store_type") != store_type:
                    continue
                ts = v.get("timestamp", 0)
                if since_ts is not None and ts < since_ts:
                    continue
                if until_ts is not None and ts > until_ts:
                    continue
                results.append(v)

            # 合并 SQLite 持久层数据（跨重启保留，InMemoryStore 为空时尤其需要）
            db_rows = self._db_query(session_id, store_type, since_ts, until_ts, limit)
            # 去重：优先 InMemoryStore 结果，SQLite 补充缺失的
            seen_keys = set()
            for r in results:
                seen_keys.add(json.dumps(r, ensure_ascii=False, sort_keys=True))
            for row in db_rows:
                row_key = json.dumps(row, ensure_ascii=False, sort_keys=True)
                if row_key in seen_keys:
                    continue
                seen_keys.add(row_key)
                results.append(row)

            # 按时间降序
            results.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
            return results[:limit]
        except Exception as e:
            logger.warning(f"查询奖励历史失败: {e}")
            return []

    async def get_high_reward_examples(
        self,
        min_score: int = 5,
        limit: int = 5,
        session_id: str = "default",
        since_hours: float = 24.0,
    ) -> List[Dict[str, Any]]:
        """
        获取高奖励回复样本（供 feedback_injector 使用）

        只返回 store_type == "response_reward" 且 total_score >= min_score 的记录。

        Args:
            min_score: 最低奖励分阈值
            limit: 返回条数上限
            session_id: 会话标识
            since_hours: 只取最近 N 小时内的数据

        Returns:
            [{"response_text": str, "total_score": int, "events": [...], "reason": str}, ...]
        """
        try:
            since_ts = time.time() - since_hours * 3600
            records = await self.get_reward_history(
                session_id=session_id,
                since_ts=since_ts,
                limit=500,
                store_type="response_reward",
            )

            # 过滤 + 排序
            high_reward = [
                r for r in records
                if r.get("store_type") == "response_reward"
                and r.get("total_score", 0) >= min_score
            ]
            # 按 total_score 降序
            high_reward.sort(key=lambda r: r.get("total_score", 0), reverse=True)

            results = []
            for r in high_reward[:limit]:
                # 生成一个简短的 reason 说明为什么这条被认为是好回复
                reasons = []
                for evt in r.get("events", []):
                    src = evt.get("source", "")
                    if src == "gift":
                        reasons.append(f"收到礼物 {evt.get('content', '')}")
                    elif src == "sc":
                        reasons.append(f"收到 SC")
                    elif src == "danmu_pos":
                        reasons.append(f"弹幕好评")
                    elif src == "density_spike":
                        reasons.append(f"弹幕密度突增")
                reason_text = "、".join(reasons[:3]) if reasons else "综合好评"

                results.append({
                    "response_text": r.get("response_text", ""),
                    "total_score": r.get("total_score", 0),
                    "event_count": r.get("event_count", 0),
                    "reason": reason_text,
                })

            logger.info(
                f"[记忆] 查询到 {len(results)} 条高奖励样本 "
                f"(min_score={min_score}, since_hours={since_hours})"
            )
            return results

        except Exception as e:
            logger.warning(f"查询高奖励样本失败: {e}")
            return []

    # ---------- 知识存储（knowledge_store） ----------
    # namespace: ('knowledge_store', session_id)
    # 存放 knowledge_curator 提炼出的"高奖励回复模式"，供持久化学习参考

    async def save_knowledge_item(
        self, item: Dict[str, Any], session_id: str = "default"
    ) -> bool:
        """
        保存一条提炼出的知识片段（高奖励回复模式）

        Args:
            item: {text, reward_score, confidence_score, source, knowledge_type, created_at}
            session_id: 会话标识

        Returns:
            是否保存成功
        """
        try:
            key = f"k_{int(time.time() * 1000)}_{id(item) % 1000}"
            value = {
                "store_type": "knowledge",
                **item,
                "timestamp": time.time(),
            }
            await self.store.aput(('knowledge_store', session_id), key, value)
            # 同步写盘，跨重启保留自主学习知识
            self._db_write(session_id, "knowledge", value)
            return True
        except Exception as e:
            logger.warning(f"保存知识片段失败: {e}")
            return False

    async def get_knowledge_items(
        self,
        session_id: str = "default",
        since_hours: float = 24.0,
        min_reward_score: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        查询知识片段

        Args:
            session_id: 会话标识
            since_hours: 只取最近 N 小时内的数据
            min_reward_score: 只返回 reward_score 不低于此值的
            limit: 返回条数上限

        Returns:
            知识片段列表
        """
        try:
            since_ts = time.time() - since_hours * 3600
            items = await self.store.asearch(
                ('knowledge_store', session_id), query='', limit=limit
            )
            results = []
            for item in items:
                v = item.value
                if v.get("store_type") != "knowledge":
                    continue
                if v.get("timestamp", 0) < since_ts:
                    continue
                if v.get("reward_score", 0) < min_reward_score:
                    continue
                results.append(v)

            # 合并 SQLite 持久层（跨重启保留）
            db_rows = self._db_query(
                session_id, "knowledge", since_ts, None, limit
            )
            for row in db_rows:
                if row.get("timestamp", 0) < since_ts:
                    continue
                if row.get("reward_score", 0) < min_reward_score:
                    continue
                results.append(row)

            # 去重 + 按奖励分降序
            seen = set()
            deduped = []
            for r in results:
                rk = json.dumps(r, ensure_ascii=False, sort_keys=True)
                if rk in seen:
                    continue
                seen.add(rk)
                deduped.append(r)
            deduped.sort(key=lambda r: r.get("reward_score", 0), reverse=True)
            return deduped[:limit]
        except Exception as e:
            logger.warning(f"查询知识片段失败: {e}")
            return []


# ---------- 全局单例 ----------

_memory_manager_instance: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 实例"""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemoryManager()
    return _memory_manager_instance
