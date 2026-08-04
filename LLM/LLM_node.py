#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 LangGraph 框架的大模型节点模块

实现大模型对话节点，为 LLM_vtuber 项目提供模块化的大模型交互能力。
"""

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional, Dict, List, Union, Any
import logging
import time

from chat_model import ChatState, doubao_chat_node


logger = logging.getLogger(__name__)


class LLMState(ChatState):
    """
    LLM 节点状态定义，基于 ChatState 扩展
    
    字段说明：
    - 继承自 ChatState 的所有字段
    - question: 当前用户问题
    - context: 上下文信息
    - name: 消息发送者名称（对应OpenAI message格式中的name字段）
    """
    question: Optional[str] = None  # 当前用户问题
    context: Optional[str] = None   # 上下文信息
    name: Optional[str] = None      # 消息发送者名称


def llm_chat_node(state: LLMState) -> LLMState:
    """
    大模型对话节点
    
    接收用户问题，调用大模型生成回答，可嵌入到 langgraph 的图中。
    
    Args:
        state: LLM 状态对象，包含对话历史和系统提示词
    
    Returns:
        更新后的 LLM 状态，包含大模型生成的回答
    """
    try:
        logger.info("执行大模型对话节点")
        
        # 构建 ChatState 用于调用现有的聊天节点
        chat_state = ChatState(
            messages=state["messages"],
            system_prompt=state["system_prompt"],
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            api_key=state.get("api_key"),
            api_url=state.get("api_url"),
            name=state.get("name")
        )
        
        # 调用豆包聊天节点生成回答
        result = doubao_chat_node(chat_state)
        
        # 更新状态并返回
        return {
            **state,
            "response": result.get("response"),
            "messages": result.get("messages", state["messages"]),
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error")
        }
        
    except Exception as e:
        error_msg = f"大模型对话节点失败: {str(e)}"
        logger.error(error_msg)
        return {
            **state,
            "response": None,
            "error": error_msg
        }


def context_aware_qa_node(state: LLMState) -> LLMState:
    """
    上下文感知的问题回答节点
    
    结合上下文信息和用户问题，调用大模型生成回答。
    
    Args:
        state: LLM 状态对象，包含用户问题和上下文
    
    Returns:
        更新后的 LLM 状态，包含大模型生成的回答
    """
    try:
        logger.info("执行上下文感知的问题回答节点")
        
        # 提取用户问题
        question = state.get("question")
        if not question:
            # 尝试从消息历史中提取最后一条用户消息
            for msg in reversed(state.get("messages", [])):
                if msg.get("role") == "user":
                    question = msg.get("content")
                    break
            if not question:
                raise ValueError("未找到用户问题")
        
        # 构建增强的系统提示词，包含上下文信息
        enhanced_system_prompt = state["system_prompt"]
        
        # 添加上下文信息
        if state.get("context"):
            enhanced_system_prompt += f"\n\n[上下文信息]\n{state['context']}"
        
        logger.info(f"处理用户问题: {question[:50]}...")
        
        # 构建 ChatState 用于调用现有的聊天节点
        chat_state = ChatState(
            messages=state["messages"],
            system_prompt=enhanced_system_prompt,
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            name=state.get("name")
        )
        
        # 调用豆包聊天节点生成回答
        result = doubao_chat_node(chat_state)
        
        # 更新状态并返回
        return {
            **state,
            "response": result.get("response"),
            "messages": result.get("messages", state["messages"]),
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error")
        }
        
    except Exception as e:
        error_msg = f"上下文感知的问题回答节点失败: {str(e)}"
        logger.error(error_msg)
        return {
            **state,
            "response": None,
            "error": error_msg
        }


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
