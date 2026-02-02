#!/usr/bin/env python3
"""
聊天模型模块
基于langgraph实现，提供多种LLM框架的聊天节点函数
为stategraph提供聊天节点功能，图结构创建由其他程序负责
"""

from typing import Dict, Any, List, Optional
import json

# 尝试导入各种LLM框架
try:
    import openai
except ImportError:
    print("警告: OpenAI库未安装，OpenAI模型不可用")
    openai = None

try:
    from doubao import Doubao
except ImportError:
    print("警告: 豆包库未安装，豆包模型不可用")
    Doubao = None

try:
    import ollama
except ImportError:
    print("警告: Ollama库未安装，Ollama模型不可用")
    ollama = None


# 定义聊天状态
class ChatState:
    """
    聊天状态类，用于在langgraph中传递数据
    可被其他程序导入和使用
    """
    def __init__(self, messages: Optional[List[Dict[str, str]]] = None, 
                 config: Optional[Dict[str, Any]] = None):
        self.messages = messages or []
        self.config = config or {}
        self.response = ""
        self.model = ""
        self.tokens_used = 0
        self.error = None


# ------------------------------
# 聊天节点函数 - 可被其他程序作为节点使用
# ------------------------------

def openai_chat_node(state: ChatState) -> ChatState:
    """
    OpenAI聊天节点函数
    可直接作为langgraph的节点使用
    
    Args:
        state: 当前聊天状态，包含messages和config
        
    Returns:
        ChatState: 更新后的聊天状态
    """
    if openai is None:
        state.error = "OpenAI库未安装"
        return state
    
    try:
        # 设置API密钥
        if "api_key" in state.config:
            openai.api_key = state.config["api_key"]
        
        if "base_url" in state.config:
            openai.api_base = state.config["base_url"]
        
        # 调用OpenAI API
        response = openai.chat.completions.create(
            model=state.config.get("model", "gpt-3.5-turbo"),
            messages=state.messages,
            temperature=state.config.get("temperature", 0.7),
            max_tokens=state.config.get("max_tokens", 1024),
            top_p=state.config.get("top_p", 1.0),
            frequency_penalty=state.config.get("frequency_penalty", 0.0),
            presence_penalty=state.config.get("presence_penalty", 0.0)
        )
        
        # 更新状态
        state.response = response.choices[0].message.content
        state.model = response.model
        state.tokens_used = response.usage.total_tokens
        state.error = None
        
        # 将回复添加到消息历史
        state.messages.append({
            "role": "assistant",
            "content": state.response
        })
        
    except Exception as e:
        state.error = f"OpenAI API错误: {str(e)}"
        state.response = ""
        state.tokens_used = 0
    
    return state


def doubao_chat_node(state: ChatState) -> ChatState:
    """
    豆包聊天节点函数
    可直接作为langgraph的节点使用
    
    Args:
        state: 当前聊天状态，包含messages和config
        
    Returns:
        ChatState: 更新后的聊天状态
    """
    if Doubao is None:
        state.error = "豆包库未安装"
        return state
    
    try:
        # 创建豆包客户端
        doubao = Doubao(
            api_key=state.config["api_key"],
            secret_key=state.config["secret_key"]
        )
        
        # 调用豆包API
        response = doubao.chat.completions.create(
            model=state.config.get("model", "doubao-pro"),
            messages=state.messages,
            temperature=state.config.get("temperature", 0.7),
            max_tokens=state.config.get("max_tokens", 1024)
        )
        
        # 更新状态
        state.response = response.choices[0].message.content
        state.model = response.model
        state.tokens_used = response.usage.total_tokens
        state.error = None
        
        # 将回复添加到消息历史
        state.messages.append({
            "role": "assistant",
            "content": state.response
        })
        
    except Exception as e:
        state.error = f"豆包API错误: {str(e)}"
        state.response = ""
        state.tokens_used = 0
    
    return state


def ollama_chat_node(state: ChatState) -> ChatState:
    """
    Ollama聊天节点函数
    可直接作为langgraph的节点使用
    
    Args:
        state: 当前聊天状态，包含messages和config
        
    Returns:
        ChatState: 更新后的聊天状态
    """
    if ollama is None:
        state.error = "Ollama库未安装"
        return state
    
    try:
        # 调用Ollama API
        response = ollama.chat(
            model=state.config.get("model", "llama2"),
            messages=state.messages,
            options={
                "temperature": state.config.get("temperature", 0.7),
                "num_predict": state.config.get("max_tokens", 1024),
                "top_p": state.config.get("top_p", 1.0)
            }
        )
        
        # 更新状态
        state.response = response["message"]["content"]
        state.model = state.config.get("model", "llama2")
        state.tokens_used = response.get("eval_count", 0)
        state.error = None
        
        # 将回复添加到消息历史
        state.messages.append({
            "role": "assistant",
            "content": state.response
        })
        
    except Exception as e:
        state.error = f"Ollama API错误: {str(e)}"
        state.response = ""
        state.tokens_used = 0
    
    return state


# ------------------------------
# 辅助函数 - 方便测试和使用
# ------------------------------

def chat_with_llm(llm_type: str, messages: List[Dict[str, str]], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    简化的聊天接口，方便测试
    
    Args:
        llm_type: LLM类型 ("openai", "doubao", "ollama")
        messages: 消息历史
        config: 配置参数
        
    Returns:
        Dict[str, Any]: 聊天结果
    """
    # 创建状态
    state = ChatState(messages, config)
    
    # 根据LLM类型调用相应的函数
    if llm_type == "openai":
        result_state = openai_chat_node(state)
    elif llm_type == "doubao":
        result_state = doubao_chat_node(state)
    elif llm_type == "ollama":
        result_state = ollama_chat_node(state)
    else:
        return {
            "success": False,
            "error": f"不支持的LLM类型: {llm_type}"
        }
    
    # 转换为字典返回
    return {
        "success": result_state.error is None,
        "response": result_state.response,
        "model": result_state.model,
        "tokens_used": result_state.tokens_used,
        "messages": result_state.messages,
        "error": result_state.error
    }


# ------------------------------
# 示例用法
# ------------------------------

if __name__ == "__main__":
    """
    示例用法 - 展示如何在其他程序中使用这些节点函数
    """
    print("=== 聊天模型节点函数测试 ===")
    
    # 配置示例
    configs = {
        "openai": {
            "api_key": "your_openai_api_key",
            "model": "gpt-3.5-turbo"
        },
        "doubao": {
            "api_key": "your_doubao_api_key",
            "secret_key": "your_doubao_secret_key",
            "model": "doubao-pro"
        },
        "ollama": {
            "model": "llama2"
        }
    }
    
    # 测试消息
    test_messages = [
        {"role": "system", "content": "你是一个帮助用户的助手。"},
        {"role": "user", "content": "你好，能介绍一下你自己吗？"}
    ]
    
    # 测试OpenAI
    if openai:
        print("\n1. 测试OpenAI节点函数:")
        state = ChatState(test_messages.copy(), configs["openai"])
        result_state = openai_chat_node(state)
        if result_state.error is None:
            print(f"回复: {result_state.response}")
            print(f"模型: {result_state.model}")
            print(f"Token使用: {result_state.tokens_used}")
        else:
            print(f"错误: {result_state.error}")
    
    # 测试豆包
    if Doubao:
        print("\n2. 测试豆包节点函数:")
        state = ChatState(test_messages.copy(), configs["doubao"])
        result_state = doubao_chat_node(state)
        if result_state.error is None:
            print(f"回复: {result_state.response}")
            print(f"模型: {result_state.model}")
            print(f"Token使用: {result_state.tokens_used}")
        else:
            print(f"错误: {result_state.error}")
    
    # 测试Ollama
    if ollama:
        print("\n3. 测试Ollama节点函数:")
        state = ChatState(test_messages.copy(), configs["ollama"])
        result_state = ollama_chat_node(state)
        if result_state.error is None:
            print(f"回复: {result_state.response}")
            print(f"模型: {result_state.model}")
            print(f"Token使用: {result_state.tokens_used}")
        else:
            print(f"错误: {result_state.error}")
    
    print("\n=== 在其他程序中使用示例 ===")
    print("""
# 示例：在其他程序中使用这些节点函数创建langgraph图
from langgraph.graph import StateGraph
from LLM.chat_model import ChatState, openai_chat_node, doubao_chat_node, ollama_chat_node

# 创建状态图
graph = StateGraph(ChatState)

# 添加节点
graph.add_node("openai_chat", openai_chat_node)
graph.add_node("doubao_chat", doubao_chat_node)
graph.add_node("ollama_chat", ollama_chat_node)

# 设置边和入口/出口节点
# ...（根据需求配置）

# 编译图
app = graph.compile()

# 使用图
result = app.invoke({
    "messages": [{"role": "user", "content": "你好！"}],
    "config": {
        "api_key": "your_api_key",
        "model": "gpt-3.5-turbo"
    }
})
    """)
