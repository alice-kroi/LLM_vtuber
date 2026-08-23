#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聊天模型模块 - 基于 LangGraph 的 OpenAI 兼容 API 调用

通用逻辑：
- 构建 messages（system + history）
- 通过 OpenAI SDK 调用兼容的 API（含超时、重试、简单的长度保护）
- 返回 ChatState 或 状态字典（按原有调用约定）
"""

import json
import logging
import os
import time
from typing import Optional

from langgraph.graph import START, END
from langgraph.graph import StateGraph
from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

# --------- 常量配置 ---------
# API调用超时：连接5s + 读60s，避免挂死
API_CONNECT_TIMEOUT = 5.0
API_READ_TIMEOUT = 60.0

# 重试：瞬态网络错误/限流
MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 0.8

# 安全的对话历史字符上限（粗略保护，避免超长请求）
# 如果 messages 总字符过大，截断最老的 user/assistant 消息直到满足
MAX_HISTORY_CHARS = 20000


class ChatState(TypedDict):
    """
    langgraph聊天状态定义 - 使用TypedDict确保类型安全

    字段说明：
    - messages: 对话历史，包含所有消息
    - system_prompt: 系统提示词，定义AI的角色和行为
    - question: 当前用户问题
    - response: 最新响应内容
    - model: 使用的模型名称
    - temperature: 温度参数，控制输出随机性
    - max_tokens: 最大生成token数
    - error: 错误信息（如有）
    - tokens_used: 使用的token数
    - name: 消息发送者名称（对应OpenAI message格式中的name字段）
    """
    messages: list[AnyMessage]
    system_prompt: str
    response: Optional[str] = None
    model: str = "doubao-seed-1-8-251228"
    temperature: float = 0.7
    max_tokens: int = 1024
    error: Optional[str] = None
    tokens_used: int = 0
    api_key: Optional[str] = os.getenv("Doubao_API_KEY")
    tts_played: bool = False
    tts_duration: float = 0.0
    tts_tone: Optional[str] = None
    tts_content: Optional[str] = None
    tts_error: Optional[str] = None
    tone: Optional[str] = None
    content: Optional[str] = None
    api_url: Optional[str] = os.getenv("Doubao_API_URL")
    name: Optional[str] = None
    question: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    storage_success: Optional[bool] = None
    storage_time: Optional[float] = None


# --------- 通用工具 ---------
def _build_openai_messages(state: ChatState) -> list:
    """
    从 ChatState 构建 OpenAI 兼容的消息列表，并做长度保护（截断老消息）
    """
    # 系统提示词
    messages_pre = []
    system_prompt = state.get("system_prompt", "")
    if system_prompt:
        messages_pre.append({"role": "system", "content": system_prompt})

    # 对话历史
    history_msgs = []
    state_messages = state.get("messages", []) or []
    for msg in state_messages:
        role = msg.role if hasattr(msg, "role") else (msg.get("role") if isinstance(msg, dict) else "user")
        content = msg.content if hasattr(msg, "content") else (msg.get("content") if isinstance(msg, dict) else str(msg))
        name_attr = msg.name if hasattr(msg, "name") else None
        if name_attr is None and isinstance(msg, dict):
            name_attr = msg.get("name") or state.get("name")
        else:
            name_attr = name_attr or state.get("name")

        item: dict = {"role": role, "content": content}
        if name_attr:
            item["name"] = name_attr
        # 保留 tool_calls（assistant 消息）和 tool_call_id（tool 消息）
        if isinstance(msg, dict):
            if msg.get("tool_calls"):
                item["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                item["tool_call_id"] = msg["tool_call_id"]
        history_msgs.append(item)

    # 长度保护：从最老的历史开始丢，直到总字符量<=上限
    while system_prompt and history_msgs:
        total = len(system_prompt) + sum(len(m.get("content", "")) for m in history_msgs)
        if total <= MAX_HISTORY_CHARS:
            break
        # 丢最老的非system消息（保留最近的）
        history_msgs.pop(0)

    return messages_pre + history_msgs


def _parse_usage(usage_obj) -> int:
    """从OpenAI响应中解析总token数"""
    if not usage_obj:
        return 0
    total = getattr(usage_obj, "total_tokens", None)
    if total is not None:
        return int(total)
    prompt_t = getattr(usage_obj, "prompt_tokens", 0) or 0
    complete_t = getattr(usage_obj, "completion_tokens", 0) or 0
    return int(prompt_t) + int(complete_t)


def _extract_error_details(e: Exception) -> str:
    """从 OpenAI SDK 异常中提取更详细的错误信息"""
    details = ""
    resp_text = None
    if hasattr(e, "response") and e.response is not None:
        try:
            resp_text = getattr(e.response, "text", None)
        except Exception:
            resp_text = None
    if resp_text:
        try:
            err = json.loads(resp_text).get("error")
            if err:
                details = f" - 详情: {err}" if isinstance(err, str) else f" - 详情: {json.dumps(err, ensure_ascii=False)}"
        except Exception:
            details = f" - 详情: {resp_text[:200]}"
    return details


def _call_chat_api_with_retry(state: ChatState, api_key: str, base_url: str,
                               tools: list = None) -> dict:
    """
    调用 OpenAI 兼容聊天 API，带超时和瞬态错误重试。
    返回 {"response": str, "tokens_used": int, "tool_calls": list|None}，失败则抛出异常。

    Args:
        tools: OpenAI function calling 格式的工具列表，None 表示不传 tools
    """
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=(API_CONNECT_TIMEOUT, API_READ_TIMEOUT),
        max_retries=0  # 我们自己管理重试，有更细的控制
    )

    openai_messages = _build_openai_messages(state)

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            kwargs = {
                "model": state["model"],
                "messages": openai_messages,
                "temperature": state["temperature"],
                "max_tokens": state["max_tokens"],
                "top_p": state.get("top_p", 1.0),
                "frequency_penalty": state.get("frequency_penalty", 0.0),
                "presence_penalty": state.get("presence_penalty", 0.0),
                "stream": False
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp_obj = client.chat.completions.create(**kwargs)
            msg = resp_obj.choices[0].message
            ai_resp = ""
            if hasattr(msg, "content") and msg.content:
                ai_resp = msg.content
            elif hasattr(msg, "reasoning_content") and msg.reasoning_content:
                ai_resp = msg.reasoning_content

            # 解析 tool_calls
            tool_calls = None
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = []
                for tc in msg.tool_calls:
                    tool_calls.append({
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments) if tc.function.arguments else {}
                    })

            tokens = _parse_usage(getattr(resp_obj, "usage", None))
            return {"response": ai_resp, "tokens_used": tokens, "tool_calls": tool_calls}

        except (APIConnectionError, RateLimitError, APITimeoutError, APIError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    f"[LLM] API瞬态错误(type={type(e).__name__}), "
                    f"重试{attempt+1}/{MAX_RETRIES}, {backoff:.1f}s后..."
                )
                time.sleep(backoff)
            else:
                raise
        except Exception as e:
            # 非瞬态错误不重试：直接抛
            raise

    # 理论不可达
    raise last_exc if last_exc else RuntimeError("未知错误")


# --------- 两个聊天节点（保留原有的返回结构约定） ---------
def openai_chat_node(state: ChatState) -> ChatState:
    """OpenAI API聊天节点"""
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_URL")

    try:
        if not api_key:
            raise ValueError("环境变量 OPENAI_API_KEY 未设置")

        result = _call_chat_api_with_retry(
            state,
            api_key=api_key,
            base_url=api_base if api_base else "https://api.openai.com/v1/"
        )

        return ChatState(
            messages=state["messages"],
            system_prompt=state["system_prompt"],
            question=state.get("question"),
            response=result["response"],
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            error=None,
            tokens_used=result["tokens_used"]
        )

    except Exception as e:
        err = f"OpenAI API调用失败: {str(e)}{_extract_error_details(e)}"
        logger.error(f"[LLM] {err}")
        return ChatState(
            **state,
            response="",
            error=err
        )


def doubao_chat_node(state: ChatState, tools: list = None) -> dict:
    """
    豆包API聊天节点
    注意：返回 dict（与原有约定一致），而不是 ChatState。
    区别于 openai_chat_node：会把 AI 回复追加到 messages 列表中。

    Args:
        tools: OpenAI function calling 格式的工具列表，None 表示不传 tools
    """
    try:
        api_key = os.getenv("Doubao_API_KEY")
        api_base = os.getenv("Doubao_API_URL", "https://api.doubao.com/v1/")

        if not api_key:
            raise ValueError("环境变量 Doubao_API_KEY 未设置")

        result = _call_chat_api_with_retry(state, api_key=api_key, base_url=api_base,
                                           tools=tools)

        logger.info(f"[LLM] 豆包调用成功: tokens={result['tokens_used']}, 响应长度={len(result['response'])}")

        # 如果有 tool_calls，不把 assistant 消息追加到 messages（由调用方处理 tool 消息）
        tool_calls = result.get("tool_calls")
        if tool_calls:
            return {
                **state,
                "response": result["response"],
                "tokens_used": result["tokens_used"],
                "tool_calls": tool_calls,
                "error": None
            }

        return {
            **state,
            "messages": list(state.get("messages", [])) + [
                {"role": "assistant", "content": result["response"]}
            ],
            "response": result["response"],
            "tokens_used": result["tokens_used"],
            "tool_calls": None,
            "error": None
        }

    except Exception as e:
        err = f"豆包API调用失败: {str(e)}{_extract_error_details(e)}"
        logger.error(f"[LLM] {err}")
        return {
            **state,
            "response": "",
            "tool_calls": None,
            "error": err
        }



if __name__ == "__main__":
    """
    豆包节点图测试主函数
    """
    try:
        # 创建状态图
        graph = StateGraph(ChatState)
        
        # 添加豆包聊天节点
        graph.add_node("node_a", doubao_chat_node)
        
        # 设置图结构
        graph.add_edge(START, "node_a")
        graph.add_edge("node_a", END)
        
        # 编译图
        app = graph.compile()
    except Exception as e:
        print(f"创建状态图失败: {str(e)}")
        exit(1)
    result1 = app.invoke({
            "messages": [{"role": "user", "content": "你好，我是一名开发者。"}],
            "model": "doubao-seed-1-8-251228",
            "temperature": 0.7,
            "max_tokens": 500,
            "system_prompt": "你是一个友好的AI助手，用简洁明了的语言回答用户的问题。"
        })
    print(result1)