#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上下文控制模块

提供完整的上下文管理功能：
1. 上下文窗口控制 - 限制 Token 使用量，避免超出模型限制
2. 历史压缩 - 对长对话进行摘要压缩，保留关键信息
3. 智能选择 - 根据相关性和重要性选择历史消息
4. 优先级管理 - 系统提示 > 身份设定 > 长期记忆 > 短期对话
"""

import os
import re
import time
import logging
import configparser
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


def _load_context_config():
    """从 config.ini 加载上下文配置"""
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini')
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    return config


_config = _load_context_config()


class ContextConfig:
    """上下文配置"""

    # Token 限制
    MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", _config.get("context", "max_tokens", fallback="4096")))
    SYSTEM_RESERVED_TOKENS = int(os.getenv("CONTEXT_SYSTEM_RESERVED", _config.get("context", "system_reserved_tokens", fallback="512")))
    MAX_HISTORY_MESSAGES = int(os.getenv("CONTEXT_MAX_HISTORY", _config.get("context", "max_history_messages", fallback="20")))

    # 压缩配置
    SUMMARIZER_ENABLED = os.getenv("CONTEXT_SUMMARIZER", _config.get("context", "summarizer_enabled", fallback="true")).lower() == "true"
    SUMMARIZER_THRESHOLD = int(os.getenv("CONTEXT_SUMMARIZER_THRESHOLD", _config.get("context", "summarizer_threshold", fallback="3000")))

    # 检索增强
    RAG_ENABLED = os.getenv("CONTEXT_RAG_ENABLED", _config.get("context", "rag_enabled", fallback="true")).lower() == "true"
    RAG_MAX_DOCUMENTS = int(os.getenv("CONTEXT_RAG_MAX_DOCS", _config.get("context", "rag_max_documents", fallback="3")))

    # 短期记忆
    MEMORY_ENABLED = os.getenv("CONTEXT_MEMORY_ENABLED", _config.get("context", "memory_enabled", fallback="true")).lower() == "true"
    MEMORY_MAX_MESSAGES = int(os.getenv("CONTEXT_MEMORY_MAX", _config.get("context", "memory_max_messages", fallback="10")))

    # 优先级权重（越大越重要）
    PRIORITY = {
        "system": 100,       # 系统提示（不可省略）
        "identity": 90,      # 身份设定
        "knowledge": 80,     # 知识库检索
        "long_term": 70,     # 长期记忆
        "recent": 60,        # 近期对话
        "historical": 50,    # 历史对话
        "summary": 40,       # 压缩摘要
    }


def estimate_tokens(text: str) -> int:
    """估算文本的 Token 数量（中文约 1 字符/Token，英文约 4 字符/Token，含标点和空格）"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars + other_chars / 4)


def estimate_message_tokens(message: Dict[str, Any]) -> int:
    """估算单条消息的 Token 数量"""
    parts = []
    role = message.get("role", "")
    content = message.get("content", "")
    if role:
        parts.append(f"Role: {role}")
    if content:
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
    return estimate_tokens("\n".join(parts))


class ContextWindowManager:
    """上下文窗口管理器"""

    def __init__(self, max_tokens: int = None):
        self.max_tokens = max_tokens or ContextConfig.MAX_TOKENS
        self.reserved_tokens = ContextConfig.SYSTEM_RESERVED_TOKENS
        self.available_tokens = self.max_tokens - self.reserved_tokens

    def calculate_budget(self, system_tokens: int = 0) -> Dict[str, int]:
        """计算上下文预算分配"""
        remaining = self.max_tokens - system_tokens - self.reserved_tokens

        return {
            "total": self.max_tokens,
            "system_reserved": self.reserved_tokens,
            "system_used": system_tokens,
            "available": max(0, remaining),
            "rag_budget": int(remaining * 0.3),      # 30% 给 RAG 检索
            "history_budget": int(remaining * 0.5),   # 50% 给对话历史
            "memory_budget": int(remaining * 0.2),    # 20% 给短期记忆
        }

    def trim_to_budget(self, messages: List[Dict], budget: int) -> List[Dict]:
        """将消息列表裁剪到 Token 预算内"""
        if not messages:
            return []

        current_tokens = 0
        result = []

        for msg in reversed(messages):
            msg_tokens = estimate_message_tokens(msg)
            if current_tokens + msg_tokens <= budget:
                result.insert(0, msg)
                current_tokens += msg_tokens
            else:
                break

        return result


class ContextSummarizer:
    """历史对话压缩器"""

    @staticmethod
    def should_summarize(messages: List[Dict], threshold: int = None) -> bool:
        """判断是否需要压缩"""
        if not ContextConfig.SUMMARIZER_ENABLED:
            return False

        threshold = threshold or ContextConfig.SUMMARIZER_THRESHOLD
        total_tokens = sum(estimate_message_tokens(m) for m in messages)
        return total_tokens > threshold

    @staticmethod
    def create_summary(messages: List[Dict]) -> Dict[str, Any]:
        """
        生成对话摘要（使用简单的规则压缩）

        对于 vtuber 场景，保留：
        - 关键话题转变
        - 用户情感变化
        - 重要事实信息
        """
        if not messages:
            return {"summary": "", "key_points": []}

        key_points = []
        dialogue_summary = []

        # 提取关键信息
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if not content or not isinstance(content, str):
                continue

            # 标记重要消息
            is_important = False
            importance_markers = [
                "你好", "再见", "谢谢", "喜欢", "讨厌",
                "帮助", "问题", "答案", "介绍", "记住",
                "我是", "我叫", "我的", "我想", "我要"
            ]
            for marker in importance_markers:
                if marker in content:
                    is_important = True
                    break

            if is_important:
                key_points.append({
                    "role": role,
                    "content": content[:100],
                    "timestamp": msg.get("timestamp", time.time())
                })

        # 生成摘要文本
        summary_parts = []
        if key_points:
            recent_topics = set()
            for kp in key_points[-5:]:  # 最近 5 个关键点
                topic = kp["content"][:30]
                if topic not in recent_topics:
                    recent_topics.add(topic)
                    role_prefix = "用户" if kp["role"] == "user" else "助手"
                    summary_parts.append(f"{role_prefix}: {kp['content'][:80]}")

        summary = "；".join(summary_parts) if summary_parts else "之前的对话"

        return {
            "summary": summary,
            "key_points": key_points,
            "message_count": len(messages)
        }

    @staticmethod
    def compress_messages(messages: List[Dict], max_keep: int = 10) -> Tuple[List[Dict], Dict]:
        """
        压缩消息列表：保留最近的消息，将历史压缩为摘要

        Returns:
            (压缩后的消息列表, 摘要信息)
        """
        if len(messages) <= max_keep:
            return messages, {"summary": "", "compressed": False}

        # 保留最近的消息
        recent = messages[-max_keep:]
        older = messages[:-max_keep]

        # 生成历史摘要
        summary = ContextSummarizer.create_summary(older)

        # 创建摘要消息
        summary_message = {
            "role": "system",
            "content": f"[历史对话摘要] {summary['summary']}",
            "type": "summary",
            "compressed_count": len(older),
            "timestamp": time.time()
        }

        result = [summary_message] + recent
        return result, {**summary, "compressed": True, "original_count": len(messages)}


class ContextSelector:
    """上下文选择器 - 智能选择最重要的上下文"""

    @staticmethod
    def score_message(message: Dict[str, Any], current_query: str = "") -> float:
        """计算消息的重要性分数"""
        score = 0.0
        role = message.get("role", "")
        content = message.get("content", "")

        # 角色权重
        role_weights = {
            "system": 10.0,
            "assistant": 6.0,
            "user": 5.0,
        }
        score += role_weights.get(role, 1.0)

        # 内容长度奖励（适当长度更好）
        if content and isinstance(content, str):
            length = len(content)
            if 20 <= length <= 200:
                score += 2.0
            elif length > 200:
                score += 1.0

            # 查询相关性（简单的关键词匹配）
            if current_query:
                query_words = set(current_query.lower().split())
                content_words = set(content.lower().split())
                overlap = query_words & content_words
                score += len(overlap) * 0.5

        # 时间衰减（较新的消息更重要）
        timestamp = message.get("timestamp")
        if timestamp:
            if isinstance(timestamp, (int, float)):
                age_hours = (time.time() - timestamp) / 3600
                time_decay = max(0.1, 1.0 - age_hours / 24)  # 24小时衰减
                score *= time_decay

        return score

    @staticmethod
    def select_context(
        messages: List[Dict],
        current_query: str = "",
        max_tokens: int = None,
        system_message: Dict = None
    ) -> List[Dict]:
        """
        智能选择上下文

        策略：
        1. 始终保留系统消息
        2. 保留最近的对话窗口
        3. 在预算内添加高相关性历史
        """
        max_tokens = max_tokens or ContextConfig.MAX_TOKENS

        selected = []
        current_tokens = 0

        # 1. 始终保留系统消息
        if system_message:
            selected.append(system_message)
            current_tokens += estimate_message_tokens(system_message)

        # 2. 分离消息类型
        system_msgs = [m for m in messages if m.get("role") == "system"]
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]

        # 3. 添加系统消息
        for msg in system_msgs:
            if msg.get("type") != "summary":  # 摘要消息后面处理
                tokens = estimate_message_tokens(msg)
                if current_tokens + tokens <= max_tokens * 0.3:
                    selected.append(msg)
                    current_tokens += tokens

        # 4. 交替添加用户和助手消息（最近优先）
        user_idx = len(user_msgs) - 1
        assistant_idx = len(assistant_msgs) - 1
        turn = 0

        while user_idx >= 0 or assistant_idx >= 0:
            # 检查预算
            budget_remaining = max_tokens - current_tokens
            if budget_remaining <= 100:
                break

            # 交替添加
            if turn % 2 == 0 and user_idx >= 0:
                msg = user_msgs[user_idx]
                tokens = estimate_message_tokens(msg)
                if current_tokens + tokens <= max_tokens:
                    selected.insert(1 if system_message else 0, msg)
                    current_tokens += tokens
                user_idx -= 1
            elif assistant_idx >= 0:
                msg = assistant_msgs[assistant_idx]
                tokens = estimate_message_tokens(msg)
                if current_tokens + tokens <= max_tokens:
                    selected.insert(1 if system_message else 0, msg)
                    current_tokens += tokens
                assistant_idx -= 1
            else:
                break

            turn += 1

        return selected

    @staticmethod
    def get_context_statistics(messages: List[Dict]) -> Dict[str, Any]:
        """获取上下文统计信息"""
        total_tokens = sum(estimate_message_tokens(m) for m in messages)
        role_counts = {}
        for msg in messages:
            role = msg.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        return {
            "total_messages": len(messages),
            "total_tokens": total_tokens,
            "role_counts": role_counts,
            "avg_tokens_per_msg": total_tokens / max(1, len(messages)),
            "context_usage_percent": round(total_tokens / ContextConfig.MAX_TOKENS * 100, 1)
        }


class ContextController:
    """上下文控制器 - 主入口"""

    def __init__(self):
        self.window_manager = ContextWindowManager()
        self.summarizer = ContextSummarizer()
        self.selector = ContextSelector()
        self.config = ContextConfig()

    def process_context(
        self,
        messages: List[Dict],
        current_query: str = "",
        system_prompt: str = "",
        additional_context: str = ""
    ) -> Dict[str, Any]:
        """
        处理上下文 - 完整的上下文控制流程

        Args:
            messages: 对话历史消息
            current_query: 当前用户查询
            system_prompt: 系统提示词
            additional_context: 额外上下文（如 RAG 结果）

        Returns:
            处理后的上下文和统计信息
        """
        original_count = len(messages)
        steps_log = []

        # 1. 计算 Token 预算
        system_tokens = estimate_tokens(system_prompt)
        budget = self.window_manager.calculate_budget(system_tokens)
        steps_log.append(f"预算计算: 总计{self.config.MAX_TOKENS}token, 可用{budget['available']}token")

        # 2. 检查是否需要压缩
        total_tokens = sum(estimate_message_tokens(m) for m in messages)
        if self.summarizer.should_summarize(messages):
            messages, summary_info = self.summarizer.compress_messages(
                messages,
                max_keep=self.config.MEMORY_MAX_MESSAGES
            )
            steps_log.append(f"历史压缩: {summary_info.get('original_count', 0)}条 -> {len(messages)}条")

        # 3. 智能选择上下文
        system_message = {
            "role": "system",
            "content": system_prompt,
            "type": "system_prompt"
        } if system_prompt else None

        selected = self.selector.select_context(
            messages=messages,
            current_query=current_query,
            max_tokens=budget['history_budget'],
            system_message=system_message
        )
        steps_log.append(f"上下文选择: 保留{len(selected)}条消息")

        # 4. 添加额外上下文
        if additional_context:
            context_msg = {
                "role": "system",
                "content": f"[相关知识] {additional_context}",
                "type": "rag_context"
            }
            context_tokens = estimate_message_tokens(context_msg)
            if context_tokens <= budget['rag_budget']:
                selected.append(context_msg)
                steps_log.append(f"添加RAG上下文: {context_tokens}token")

        # 5. 最终裁剪
        final_tokens = sum(estimate_message_tokens(m) for m in selected)
        if final_tokens > self.config.MAX_TOKENS:
            selected = self.window_manager.trim_to_budget(selected, self.config.MAX_TOKENS - system_tokens)
            steps_log.append(f"最终裁剪: {final_tokens} -> {sum(estimate_message_tokens(m) for m in selected)}token")

        # 6. 生成统计
        stats = self.selector.get_context_statistics(selected)
        stats["steps"] = steps_log
        stats["original_count"] = original_count
        stats["compressed"] = len(selected) < original_count

        return {
            "messages": selected,
            "statistics": stats,
            "processed": True
        }

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            "max_tokens": self.config.MAX_TOKENS,
            "system_reserved_tokens": self.config.SYSTEM_RESERVED_TOKENS,
            "max_history_messages": self.config.MAX_HISTORY_MESSAGES,
            "summarizer_enabled": self.config.SUMMARIZER_ENABLED,
            "summarizer_threshold": self.config.SUMMARIZER_THRESHOLD,
            "rag_enabled": self.config.RAG_ENABLED,
            "rag_max_documents": self.config.RAG_MAX_DOCUMENTS,
            "memory_enabled": self.config.MEMORY_ENABLED,
            "memory_max_messages": self.config.MEMORY_MAX_MESSAGES,
        }

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置"""
        if "max_tokens" in updates:
            self.config.MAX_TOKENS = int(updates["max_tokens"])
            self.window_manager.max_tokens = self.config.MAX_TOKENS
            self.window_manager.available_tokens = self.config.MAX_TOKENS - self.config.SYSTEM_RESERVED_TOKENS

        if "system_reserved_tokens" in updates:
            self.config.SYSTEM_RESERVED_TOKENS = int(updates["system_reserved_tokens"])
            self.window_manager.reserved_tokens = self.config.SYSTEM_RESERVED_TOKENS

        if "max_history_messages" in updates:
            self.config.MAX_HISTORY_MESSAGES = int(updates["max_history_messages"])

        if "summarizer_enabled" in updates:
            self.config.SUMMARIZER_ENABLED = bool(updates["summarizer_enabled"])

        if "summarizer_threshold" in updates:
            self.config.SUMMARIZER_THRESHOLD = int(updates["summarizer_threshold"])

        if "rag_enabled" in updates:
            self.config.RAG_ENABLED = bool(updates["rag_enabled"])

        if "memory_enabled" in updates:
            self.config.MEMORY_ENABLED = bool(updates["memory_enabled"])

        logger.info(f"上下文配置已更新: {updates}")
        return self.get_config()


# ---------- 全局单例 ----------

_context_controller_instance: Optional[ContextController] = None


def get_context_controller() -> ContextController:
    """获取全局 ContextController 实例"""
    global _context_controller_instance
    if _context_controller_instance is None:
        _context_controller_instance = ContextController()
    return _context_controller_instance
