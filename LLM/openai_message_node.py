#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI 消息格式节点模块

实现一个符合 OPENAI API 消息格式规范的功能节点，用于处理和生成符合规范的消息。
"""

from typing import Dict, Any, List
import logging
import time
from openai import OpenAI
import os

from message_state import MessageState, openai_format_message_state, add_message_to_state
from data_persistence import get_data_persistence


logger = logging.getLogger(__name__)


def openai_message_node(state: MessageState) -> MessageState:
    """
    OpenAI 消息格式处理节点
    
    接收消息状态，将其转换为 OpenAI API 兼容的格式，调用 OpenAI API 生成响应，
    并将响应添加回消息状态。
    
    Args:
        state: 消息状态对象
    
    Returns:
        更新后的消息状态，包含 AI 响应
    """
    try:
        logger.info("执行 OpenAI 消息格式处理节点")
        
        # 1. 检查必要的配置
        api_key = os.getenv("Doubao_API_KEY")
        logger.info(f"API Key: {api_key}")
        if not api_key:
            raise ValueError("环境变量 Doubao_API_KEY 未设置")
        logger.info("使用真实 API 调用")
        
        # 2. 创建 OpenAI 客户端
        client = OpenAI(api_key=api_key, base_url=os.getenv("Doubao_API_URL", "https://api.doubao.com/v1/"))
        
        # 3. 将消息状态转换为 OpenAI 兼容格式
        openai_messages = openai_format_message_state(state)
        logger.info(f"转换为 OpenAI 格式的消息数量: {len(openai_messages)}")
        
        # 4. 检查是否有消息需要处理
        if not openai_messages:
            logger.warning("没有消息需要处理")
            return state
        
        # 5. 调用 OpenAI API
        start_time = time.time()
        logger.info("开始调用 OpenAI API")
        logger.info(f"使用模型: {state.get('metadata', {}).get('model', 'doubao-seed-1-8-251228')}")
        logger.info(f"消息数量: {len(openai_messages)}")
        logger.info(f"消息内容: {openai_messages}")
        
        try:
            response = client.chat.completions.create(
                model=state.get("metadata", {}).get("model", "doubao-seed-1-8-251228"),
                messages=openai_messages,
                temperature=state.get("metadata", {}).get("temperature", 0.7),
                max_tokens=state.get("metadata", {}).get("max_tokens", 1000),
                timeout=10  # 减少超时时间，避免程序长时间卡住
            )
            response_time = time.time() - start_time
            logger.info(f"API 调用完成，耗时: {response_time:.2f}秒")
            logger.info(f"API 响应: {response}")
        except Exception as api_error:
            logger.error(f"API 调用失败: {api_error}")
            # 禁止使用模拟响应，直接抛出异常
            raise
        
        # 6. 解析响应
        message = response.choices[0].message
        ai_response = message.content if hasattr(message, "content") and message.content else ""
        
        # 7. 解析 token 使用情况
        tokens_used = 0
        if hasattr(response, "usage") and hasattr(response.usage, "total_tokens"):
            tokens_used = response.usage.total_tokens
        
        # 8. 创建 AI 响应消息
        ai_message = {
            "message_id": str(state.get("message_id", "")[:8]) + "-assistant",
            "role": "assistant",
            "content": ai_response,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vector": None,  # 向量字段，后续可以通过嵌入模型生成
            "metadata": {
                "model": state.get("metadata", {}).get("model", "gpt-3.5-turbo"),
                "tokens_used": tokens_used,
                "response_time": response_time
            },
            "status": "processed"
        }
        
        # 9. 更新消息状态
        new_state = add_message_to_state(state, ai_message)
        
        # 10. 存储 AI 消息到数据库
        data_persistence = get_data_persistence()
        if not data_persistence.store_message(ai_message):
            logger.warning("存储 AI 消息到数据库失败")
        
        # 11. 更新指标
        new_state["metrics"]["total_tokens"] += tokens_used
        if new_state["metrics"]["assistant_message_count"] > 0:
            new_state["metrics"]["average_response_time"] = (
                (new_state["metrics"]["average_response_time"] * (new_state["metrics"]["assistant_message_count"] - 1) + response_time) /
                new_state["metrics"]["assistant_message_count"]
            )
        
        # 12. 清除错误信息
        new_state["error_info"] = {
            "error_code": None,
            "error_message": None,
            "error_timestamp": None,
            "recovery_status": "pending"
        }
        
        logger.info(f"OpenAI API 调用成功，生成响应长度: {len(ai_response)}")
        return new_state
        
    except Exception as e:
        error_msg = f"OpenAI 消息格式处理节点失败: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        
        # 更新错误信息
        error_state = state.copy()
        error_state["error_info"] = {
            "error_code": "OPENAI_API_ERROR",
            "error_message": error_msg,
            "error_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recovery_status": "failed"
        }
        
        return error_state

def _get_mock_response(state: MessageState) -> MessageState:
    """
    获取模拟响应
    
    Args:
        state: 消息状态
    
    Returns:
        包含模拟响应的消息状态
    """
    logger.info("使用模拟响应")
    
    # 模拟响应内容
    mock_responses = {
        "你好，能介绍一下自己吗？": "你好！我是一个AI助手，很高兴为你服务。我可以回答问题、提供信息、帮助你完成各种任务。请问有什么我可以帮助你的吗？",
        "今天天气怎么样？": "今天天气晴朗，适合户外活动。温度适中，大约在20-25摄氏度之间。",
        "如何学习Python编程？": "学习Python编程可以从基础语法开始，然后逐步学习面向对象编程、模块和包的使用，最后学习一些常用的库和框架。",
        "推荐一部好看的电影": "推荐你看《盗梦空间》，这是一部非常精彩的科幻电影，导演是克里斯托弗·诺兰。",
        "什么是人工智能？": "人工智能是指让计算机模拟人类智能的技术，包括机器学习、深度学习、自然语言处理等多个领域。"
    }
    
    # 获取最后一条用户消息
    user_message = None
    for msg in reversed(state.get("messages", [])):
        if msg.get("role") == "user":
            user_message = msg
            break
    
    # 生成模拟响应
    if user_message:
        content = user_message.get("content", "")
        ai_response = mock_responses.get(content, "我是一个AI助手，很高兴为你服务。")
    else:
        ai_response = "我是一个AI助手，很高兴为你服务。"
    
    # 创建 AI 响应消息
    ai_message = {
        "message_id": str(state.get("message_id", "")[:8]) + "-assistant",
        "role": "assistant",
        "content": ai_response,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vector": None,  # 向量字段，后续可以通过嵌入模型生成
        "metadata": {
            "model": "mock-model",
            "tokens_used": 100,
            "response_time": 0.5
        },
        "status": "processed"
    }
    
    # 更新消息状态
    new_state = add_message_to_state(state, ai_message)
    
    # 存储 AI 消息到数据库
    data_persistence = get_data_persistence()
    if not data_persistence.store_message(ai_message):
        logger.warning("存储 AI 消息到数据库失败")
    
    # 更新指标
    new_state["metrics"]["total_tokens"] += 100
    if new_state["metrics"]["assistant_message_count"] > 0:
        new_state["metrics"]["average_response_time"] = (
            (new_state["metrics"]["average_response_time"] * (new_state["metrics"]["assistant_message_count"] - 1) + 0.5) /
            new_state["metrics"]["assistant_message_count"]
        )
    
    # 清除错误信息
    new_state["error_info"] = {
        "error_code": None,
        "error_message": None,
        "error_timestamp": None,
        "recovery_status": "pending"
    }
    
    logger.info(f"模拟响应生成成功，响应长度: {len(ai_response)}")
    return new_state


def validate_openai_message_format(messages: List[Dict[str, Any]]) -> bool:
    """
    验证消息格式是否符合 OpenAI API 规范
    
    Args:
        messages: 消息列表
    
    Returns:
        是否符合规范
    """
    try:
        for msg in messages:
            if "role" not in msg or "content" not in msg:
                return False
            if msg["role"] not in ["system", "user", "assistant", "tool"]:
                return False
            if not isinstance(msg["content"], str):
                return False
        return True
    except Exception:
        return False


def create_openai_message(role: str, content: str, **kwargs) -> Dict[str, Any]:
    """
    创建符合 OpenAI API 格式的消息
    
    Args:
        role: 消息角色
        content: 消息内容
        **kwargs: 附加参数
    
    Returns:
        符合 OpenAI API 格式的消息
    """
    message = {
        "role": role,
        "content": content
    }
    message.update(kwargs)
    return message
