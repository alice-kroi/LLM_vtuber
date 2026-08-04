#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聊天模型模块 - 使用TypedDict定义langgraph状态结构
"""

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional
import os
from openai import OpenAI
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, END
from langgraph.graph import StateGraph
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
    - tts_played: TTS是否播放成功
    - tts_duration: TTS音频时长（秒）
    - tts_tone: TTS使用的语气
    - tts_content: TTS合成的内容
    - tts_error: TTS错误信息（如有）
    - tone: 提取的语气
    - content: 提取的内容（去掉语气标记）
    """
    messages: list[AnyMessage]      # 对话历史（使用langchain的AnyMessage类型）
    system_prompt: str              # 系统提示词
    response: Optional[str] = None  # 最新响应
    model: str = "doubao-seed-1-8-251228"       # 模型名称（默认豆包）
    temperature: float = 0.7        # 温度参数
    max_tokens: int = 1024          # 最大token数
    error: Optional[str] = None     # 错误信息
    tokens_used: int = 0            # 使用的token数
    api_key: Optional[str] = os.getenv("Doubao_API_KEY")  # API密钥（如果需要）
    # TTS相关字段
    tts_played: bool = False        # TTS是否播放成功
    tts_duration: float = 0.0       # TTS音频时长（秒）
    tts_tone: Optional[str] = None  # TTS使用的语气
    tts_content: Optional[str] = None  # TTS合成的内容
    tts_error: Optional[str] = None  # TTS错误信息
    # 语气和内容字段
    tone: Optional[str] = None      # 提取的语气
    content: Optional[str] = None   # 提取的内容
    api_url: Optional[str] = os.getenv("Doubao_API_URL")  # API URL（如果需要）
    name: Optional[str] = None      # 消息发送者名称






def openai_chat_node(state: ChatState) -> ChatState:
    """
    使用OpenAI API的聊天节点函数
    
    Args:
        state: 聊天状态对象
        
    Returns:
        更新后的聊天状态，包含AI响应
    """
    try:
        # 1. 从环境变量获取OpenAI API配置
        api_key = os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("OPENAI_API_URL")  # 可选的API地址
        
        if not api_key:
            raise ValueError("环境变量 OPENAI_API_KEY 未设置")
        
        # 2. 创建OpenAI客户端
        client_params = {"api_key": api_key}
        if api_base:
            client_params["base_url"] = api_base
            
        client = OpenAI(**client_params)
        
        # 3. 构建消息列表（转换为OpenAI兼容格式）
        openai_messages = []
        
        # 添加系统提示词（如果有）
        if state["system_prompt"]:
            openai_messages.append({"role": "system", "content": state["system_prompt"]})
        
        # 添加对话历史
        for msg in state["messages"]:
            # 转换langchain消息到OpenAI消息格式
            openai_role = msg.role if hasattr(msg, "role") else "user"
            openai_content = msg.content if hasattr(msg, "content") else str(msg)
            # 检查是否有name字段
            openai_name = msg.name if hasattr(msg, "name") else state.get("name")
            # 构建消息对象
            message_obj = {"role": openai_role, "content": openai_content}
            if openai_name:
                message_obj["name"] = openai_name
            openai_messages.append(message_obj)
        
        # 4. 调用OpenAI API
        response = client.chat.completions.create(
            model=state["model"],
            messages=openai_messages,
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            top_p=state.get("top_p", 1.0),  # 可选参数
            frequency_penalty=state.get("frequency_penalty", 0.0),  # 可选参数
            presence_penalty=state.get("presence_penalty", 0.0)  # 可选参数
        )
        
        # 5. 解析响应
        message = response.choices[0].message
        # 处理消息内容 - 优先使用 content
        ai_response = message.content if hasattr(message, "content") and message.content else ""
        
        # 解析 token 使用情况
        tokens_used = response.usage.total_tokens if hasattr(response.usage, "total_tokens") else 0
        
        # 6. 可选：打印配置信息（如果提供）
        if state.get("config") and "configurable" in state.get("config", {}):
            user_id = state.get("config", {}).get("configurable", {}).get("user_id", "unknown")
            print(f"[OpenAI] 处理用户请求 - 用户ID: {user_id}")
        
        # 7. 更新状态并返回
        return ChatState(
            messages=state["messages"],  # 保持原始消息列表
            system_prompt=state["system_prompt"],
            question=state["question"],
            response=ai_response,  # 更新响应
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            error=None,  # 清除错误
            tokens_used=tokens_used  # 更新使用的token数
        )
        
    except Exception as e:
        # 处理错误并返回错误状态
        error_msg = f"OpenAI API调用失败: {str(e)}"
        
        # 获取更详细的错误信息（如果可用）
        if hasattr(e, "response") and hasattr(e.response, "text"):
            try:
                import json
                error_details = json.loads(e.response.text)
                if error_details.get("error"):
                    error_msg += f" - 详情: {error_details['error']}"
            except:
                pass
        
        return ChatState(
            **state,  # 保持其他状态不变
            response="",  # 清除响应
            error=error_msg  # 设置错误信息
        )
    


def doubao_chat_node(state: ChatState) -> ChatState:
    """
    使用豆包API的聊天节点函数（基于OpenAI兼容接口）
    
    Args:
        state: 聊天状态对象
        config: 可选的RunnableConfig（包含用户ID等配置）
        
    Returns:
        更新后的聊天状态，包含AI响应
    """
    try:
        # 1. 从环境变量获取豆包API配置
        api_key = os.getenv("Doubao_API_KEY")
        api_base = os.getenv("Doubao_API_URL", "https://api.doubao.com/v1/")
        
        if not api_key:
            raise ValueError("环境变量 Doubao_API_KEY 未设置")
        
        # 2. 创建OpenAI客户端（豆包API兼容OpenAI接口）
        client = OpenAI(
            api_key=api_key,
            base_url=api_base  # 豆包API地址
        )
        
        # 3. 构建消息列表（转换为OpenAI/豆包兼容格式）
        doubao_messages = []
        
        # 添加系统提示词（如果有）
        if state["system_prompt"]:
            doubao_messages.append({"role": "system", "content": state["system_prompt"]})
        
        # 添加对话历史
        for msg in state["messages"]:
            # 转换langchain消息到豆包兼容格式
            msg_role = msg.role if hasattr(msg, "role") else "user"
            msg_content = msg.content if hasattr(msg, "content") else str(msg)
            # 检查是否有name字段
            msg_name = msg.name if hasattr(msg, "name") else state.get("name")
            # 构建消息对象
            message_obj = {"role": msg_role, "content": msg_content}
            if msg_name:
                message_obj["name"] = msg_name
            doubao_messages.append(message_obj)
        
        # 4. 调用豆包API
        response = client.chat.completions.create(
            model=state["model"],  # 豆包模型名称
            messages=doubao_messages,
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            top_p=state.get("top_p", 1.0),  # 可选参数
            frequency_penalty=state.get("frequency_penalty", 0.0),  # 可选参数
            presence_penalty=state.get("presence_penalty", 0.0)  # 可选参数
        )
        #print(response)
        # 5. 解析响应
        message = response.choices[0].message
        # 处理豆包特有的消息结构 - 优先使用 content，然后是 reasoning_content
        ai_response = message.content if hasattr(message, "content") and message.content else getattr(message, "reasoning_content", "")
        
        # 解析 token 使用情况 - 处理不同的 usage 结构
        tokens_used = 0
        if hasattr(response, "usage"):
            if hasattr(response.usage, "total_tokens"):
                tokens_used = response.usage.total_tokens
            elif hasattr(response.usage, "completion_tokens") and hasattr(response.usage, "prompt_tokens"):
                tokens_used = response.usage.completion_tokens + response.usage.prompt_tokens
        
        
        
        # 7. 更新状态并返回
        return {
            **state,  # 保持其他状态不变
            "messages": state["messages"]+[
                {"role": "assistant", "content": ai_response}
            ],  # 添加AI响应到消息列表
            "response": ai_response,  # 更新响应
            "tokens_used": tokens_used  # 更新使用的token数
        }
        
    except Exception as e:
        # 处理错误并返回错误状态
        error_msg = f"豆包API调用失败: {str(e)}"
        
        # 获取更详细的错误信息（如果可用）
        if hasattr(e, "response") and hasattr(e.response, "text"):
            try:
                import json
                error_details = json.loads(e.response.text)
                if error_details.get("error"):
                    error_msg += f" - 详情: {error_details['error']}"
            except:
                pass
        
        return {
            **state,  # 保持其他状态不变
            "response": "",  # 清除响应
            "error": error_msg  # 设置错误信息
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