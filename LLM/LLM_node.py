#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 LangGraph 框架的大模型节点模块

提供两个对外函数（保持原有接口）：
- llm_chat_node(state): 纯聊天
- context_aware_qa_node(state): 在 system_prompt 后拼上下文，然后调用

两个函数都委托给内部的 _run_doubao_chat，避免大量重复。
"""

import logging
import json

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional

from chat_model import ChatState, doubao_chat_node

logger = logging.getLogger(__name__)

# 工具 Schema（延迟导入避免循环依赖）
_BROWSER_TOOLS = None
_VISION_TOOLS = None
_ALL_TOOLS = None


def _get_browser_tools():
    """延迟获取浏览器工具 Schema"""
    global _BROWSER_TOOLS
    if _BROWSER_TOOLS is None:
        try:
            from tool.browser_tool import BROWSER_TOOLS_SCHEMA
            _BROWSER_TOOLS = BROWSER_TOOLS_SCHEMA
        except ImportError:
            _BROWSER_TOOLS = []
            logger.warning("browser_tool 模块未安装，浏览器工具不可用")
    return _BROWSER_TOOLS


def _get_vision_tools():
    """延迟获取视觉分析工具 Schema"""
    global _VISION_TOOLS
    if _VISION_TOOLS is None:
        try:
            from tool.vision_tool import VISION_TOOLS_SCHEMA
            _VISION_TOOLS = VISION_TOOLS_SCHEMA
        except ImportError:
            _VISION_TOOLS = []
            logger.warning("vision_tool 模块未安装，视觉分析工具不可用")
    return _VISION_TOOLS


def _get_all_tools():
    """获取所有可用工具 Schema（浏览器 + 视觉）"""
    global _ALL_TOOLS
    if _ALL_TOOLS is None:
        all_tools = []
        all_tools.extend(_get_browser_tools())
        all_tools.extend(_get_vision_tools())
        _ALL_TOOLS = all_tools
        logger.info(f"已加载 {len(all_tools)} 个工具 Schema")
    return _ALL_TOOLS


class LLMState(ChatState):
    """
    LLM 节点状态定义，基于 ChatState 扩展

    字段说明：
    - 继承自 ChatState 的所有字段
    - question: 当前用户问题
    - context: 上下文信息（RAG 检索结果）
    - name: 消息发送者名称
    """
    question: Optional[str] = None
    context: Optional[str] = None
    name: Optional[str] = None


def _extract_last_user_question(state: LLMState) -> Optional[str]:
    """从状态中提取最后一条用户消息内容作为 question"""
    if state.get("question"):
        return state["question"]
    for msg in reversed(state.get("messages", []) or []):
        role = msg.role if hasattr(msg, "role") else (msg.get("role") if isinstance(msg, dict) else None)
        if role == "user":
            content = msg.content if hasattr(msg, "content") else (msg.get("content") if isinstance(msg, dict) else None)
            return content
    return None


async def _run_doubao_chat(state: LLMState, *, with_context: bool) -> dict:
    """
    统一的豆包聊天执行逻辑（异步，支持工具调用循环）。

    Args:
        state: LLM 状态
        with_context: 是否把 state["context"] 拼接到 system_prompt 末尾
    """
    try:
        logger.info(f"执行 {'上下文感知问答' if with_context else '大模型对话'} 节点")

        question = _extract_last_user_question(state)
        if not question:
            raise ValueError("未找到用户问题")

        # 构建 system prompt（可选拼上 RAG 上下文）
        system_prompt = state["system_prompt"] or ""
        if with_context and state.get("context"):
            system_prompt = f"{system_prompt}\n\n[上下文信息]\n{state['context']}".strip()

        logger.info(f"处理用户问题: {question[:50]}...")

        # 组装 ChatState（保持 name 传递）
        chat_state = ChatState(
            messages=state["messages"],
            system_prompt=system_prompt,
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            name=state.get("name")
        )

        # 第一轮调用（可能返回 tool_calls）
        tools = _get_all_tools()
        result = doubao_chat_node(chat_state, tools=tools if tools else None)

        # 如果 LLM 返回了 tool_calls，执行工具后再次调用 LLM
        tool_calls = result.get("tool_calls")
        if tool_calls:
            logger.info(f"[LLM] 检测到 {len(tool_calls)} 个工具调用: "
                        f"{[tc['name'] for tc in tool_calls]}")

            # 执行工具调用
            tool_results = await _execute_tool_calls(tool_calls)

            # 将 assistant 的 tool_call 消息和 tool 结果消息加入历史
            new_messages = list(chat_state.get("messages", []))
            # assistant 消息（带 tool_calls）
            new_messages.append({
                "role": "assistant",
                "content": result.get("response") or "",
                "tool_calls": [
                    {
                        "id": tc["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                        }
                    }
                    for tc in tool_calls
                ]
            })
            # tool 结果消息
            for tr in tool_results:
                new_messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(tr["result"], ensure_ascii=False)
                        if tr.get("result") is not None
                        else (tr.get("error") or "工具执行失败")
                })

            chat_state["messages"] = new_messages

            # 第二轮调用（不再传 tools，让模型直接生成最终回复）
            logger.info("[LLM] 工具执行完毕，进行第二轮 LLM 调用")
            result = doubao_chat_node(chat_state, tools=None)

        return {
            **state,
            "response": result.get("response"),
            "messages": result.get("messages", state["messages"]),
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error")
        }

    except Exception as e:
        tag = "上下文感知的问题回答节点" if with_context else "大模型对话节点"
        error_msg = f"{tag}失败: {str(e)}"
        logger.error(error_msg)
        return {
            **state,
            "response": None,
            "error": error_msg
        }


async def _execute_tool_calls(tool_calls: list) -> list:
    """执行工具调用列表，返回结果列表"""
    from tool.tool_node import tool_registry

    results = []
    for tc in tool_calls:
        tool_call = {
            "tool_call_id": tc["tool_call_id"],
            "name": tc["name"],
            "arguments": tc["arguments"]
        }
        logger.info(f"[LLM] 执行工具: {tc['name']}(args={tc['arguments']})")
        result = await tool_registry.execute_tool(tool_call, timeout=60.0)
        results.append(result)
        logger.info(f"[LLM] 工具结果: {tc['name']} -> "
                    f"{'成功' if result.get('result') else '失败'}")
    return results


async def llm_chat_node(state: LLMState) -> LLMState:
    """大模型对话节点（不带RAG上下文增强）"""
    return await _run_doubao_chat(state, with_context=False)


async def context_aware_qa_node(state: LLMState) -> LLMState:
    """上下文感知的问题回答节点（拼上 state['context'] 后调用 LLM）"""
    return await _run_doubao_chat(state, with_context=True)


if __name__ == "__main__":
    """
    LLM 节点图测试主函数
    """
    try:
        # 配置日志
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        print("=== 测试 LLM 节点图 ===")
        
        # 创建状态图
        from langgraph.graph import StateGraph, START, END
        graph = StateGraph(LLMState)
        
        # 添加大模型对话节点
        graph.add_node("llm_chat", llm_chat_node)
        
        # 设置图结构
        graph.add_edge(START, "llm_chat")
        graph.add_edge("llm_chat", END)
        
        # 编译图
        app = graph.compile()
        print("图编译成功！")
        
        # 测试图
        result = app.invoke({
                "messages": [{"role": "user", "content": "你好，我是一名开发者。"}],
                "model": "doubao-seed-1-8-251228",
                "temperature": 0.7,
                "max_tokens": 500,
                "system_prompt": "你是一个友好的AI助手，用简洁明了的语言回答用户的问题。"
            })
        
        print("\n=== 测试结果 ===")
        print(f"回答: {result['response']}")
        print(f"错误: {result['error']}")
        print(f"使用的模型: {result['model']}")
        
        # 打印完整对话历史
        print("\n完整对话历史:")
        for msg in result["messages"]:
            print(f"{msg['role'].capitalize()}: {msg['content']}")
        
        print("\n=== 测试完成 ===")
        
    except Exception as e:
        print(f"测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
