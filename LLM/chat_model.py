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
def _content_text_len(content) -> int:
    """统计消息文本长度；多模态 content 只算 text 部分，不把 base64 当字符数。"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(str(part.get("text", "")))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return 0


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
        total = len(system_prompt) + sum(
            _content_text_len(m.get("content")) for m in history_msgs
        )
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
                # 记录思考过程但不作为响应返回
                logger.debug(f"[LLM] reasoning_content 长度: {len(msg.reasoning_content)}, 不作为正式回复")
                ai_resp = ""

            # 解析 tool_calls
            tool_calls = None
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = []
                for tc in msg.tool_calls:
                    raw_args = tc.function.arguments if tc.function else ""
                    parsed_args, arg_err = {}, ""
                    if raw_args:
                        try:
                            parsed_args = json.loads(raw_args)
                        except Exception as e:  # noqa: BLE001
                            # 参数被 max_tokens 截断时 JSON 不闭合：不在这里崩掉整轮，
                            # 交给调用方（CodingLoop）识别并改成小步写入
                            arg_err = str(e)
                    item = {
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "arguments": parsed_args,
                    }
                    if arg_err:
                        item["arguments_error"] = arg_err
                    tool_calls.append(item)

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


# --------- P0：流式生成（首字优先） ---------
def chat_stream(state: ChatState, on_token=None, on_first_token=None,
                api_key: str = None, base_url: str = None,
                tools: list = None, json_schema: dict = None) -> dict:
    """
    流式聊天生成（P0：首字优先，降低用户感知延迟）。

    - 与 `_call_chat_api_with_retry` 结果结构一致：返回 {"response","tokens_used","tool_calls"}
    - 通过 `stream=True` 逐 chunk 累积完整文本（保证非流式路径语义不变）
    - 可选回调：on_token(chunk_text) 每片调用；on_first_token(text) 首个内容到达时调用
      （供调用方立即推送到 WebSocket，实现"首字快、尾部长"）
    - 可选 json_schema：启用豆包严格结构化输出（response_format=json_schema+strict），
      返回 dict 字段导航更稳定（P1 结构化输出）
    - 失败时抛异常由调用方降级

    Args:
        state: ChatState
        on_token: 可选，接收每个增量文本片段
        on_first_token: 可选，首个文本片段到达时触发（用于首字推送）
        api_key/base_url: 覆盖环境变量
        tools: 可选，OpenAI function calling 格式工具
        json_schema: 可选，json_schema 严格输出（格式 {"type":"object","properties":...}）

    Returns:
        {"response": str, "tokens_used": int, "tool_calls": list|None, "structured": dict|None}
    """
    _api_key = api_key or state.get("api_key") or os.getenv("Doubao_API_KEY") or os.getenv("DOUBAO_API_KEY")
    _base_url = base_url or state.get("api_url") or os.getenv("Doubao_API_URL")
    if not _api_key or not _base_url:
        raise ValueError("未配置豆包 API（Doubao_API_KEY / Doubao_API_URL）")

    client = OpenAI(api_key=_api_key, base_url=_base_url,
                    timeout=(API_CONNECT_TIMEOUT, API_READ_TIMEOUT), max_retries=0)
    openai_messages = _build_openai_messages(state)

    kwargs = {
        "model": state["model"],
        "messages": openai_messages,
        "temperature": state["temperature"],
        "max_tokens": state["max_tokens"],
        "top_p": state.get("top_p", 1.0),
        "frequency_penalty": state.get("frequency_penalty", 0.0),
        "presence_penalty": state.get("presence_penalty", 0.0),
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "structured_reply", "schema": json_schema, "strict": True},
        }

    parts = []
    reasoning_parts = []
    first_emitted = False
    tool_calls_buffer = {}  # index -> {id,name,arguments(str)}
    tool_calls = None

    for chunk in client.chat.completions.create(**kwargs):
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 内容流
        if hasattr(delta, "content") and delta.content:
            parts.append(delta.content)
            if on_token is not None:
                on_token(delta.content)
            if not first_emitted and on_first_token is not None:
                try:
                    on_first_token(delta.content)
                except Exception as e:
                    logger.debug(f"[流式] on_first_token 回调异常: {e}")
                first_emitted = True
        # 思考流（reasoning）仅记录不输出
        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            reasoning_parts.append(delta.reasoning_content)
        # 工具调用流
        if hasattr(delta, "tool_calls") and delta.tool_calls:
            tool_calls = tool_calls or []
            for tc in delta.tool_calls:
                idx = tc.index if hasattr(tc, "index") else 0
                if idx not in tool_calls_buffer:
                    tool_calls_buffer[idx] = {
                        "tool_call_id": tc.id or "",
                        "name": tc.function.name if tc.function else "",
                        "arguments": tc.function.arguments if tc.function else "",
                    }
                else:
                    buf = tool_calls_buffer[idx]
                    if tc.id:
                        buf["tool_call_id"] = tc.id
                    if tc.function and tc.function.name:
                        buf["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        buf["arguments"] += tc.function.arguments
            tool_calls = [tool_calls_buffer[i] for i in sorted(tool_calls_buffer)]

    response = "".join(parts)
    # 若只有思考无内容（给出空），保持与旧逻辑一致地丢弃思考
    if not response:
        response = ""
        if reasoning_parts:
            logger.debug(f"[流式] 仅 reason（{len(''.join(reasoning_parts))}字），不作为正式回复")

    # 规范化工具调用
    normalized_tools = None
    if tool_calls:
        normalized_tools = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except Exception:
                args = {}
            normalized_tools.append({
                "tool_call_id": tc["tool_call_id"],
                "name": tc["name"],
                "arguments": args,
            })

    # P1 结构化输出：json_schema 模式下，把累积文本解析为结构化 dict
    structured = None
    if json_schema and response:
        try:
            structured = json.loads(response)
            if not isinstance(structured, dict):
                structured = None
        except Exception:
            structured = None

    return {"response": response, "tokens_used": 0, "tool_calls": normalized_tools,
            "structured": structured}


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
        # state 可显式携带端点（V2 LLMAdapter 用它隔离主模型与视觉/embedding）
        api_key = state.get("api_key") or os.getenv("Doubao_API_KEY")
        api_base = (state.get("api_url")
                    or os.getenv("Doubao_API_URL", "https://api.doubao.com/v1/"))

        if not api_key:
            raise ValueError("环境变量 Doubao_API_KEY / state.api_key 未设置")

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