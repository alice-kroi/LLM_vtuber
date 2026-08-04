#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI 消息格式节点测试

测试 openai_message_node.py 中的功能，特别是使用豆包模型的部分。
"""

import logging
import os
from langgraph.graph import StateGraph, START, END

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from message_state import MessageState, create_initial_message_state, add_message_to_state
from openai_message_node import openai_message_node, validate_openai_message_format, create_openai_message
from prompt.prompt_load import PromptTemplateLoader


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_message_state_creation():
    """
    测试消息状态创建
    """
    logger.info("测试消息状态创建...")
    
    # 创建初始消息状态
    state = create_initial_message_state()
    
    # 验证状态结构
    assert "message_id" in state
    assert "messages" in state
    assert "timestamps" in state
    assert "metrics" in state
    assert "error_info" in state
    
    logger.info(f"消息状态创建成功，消息ID: {state['message_id']}")
    logger.info(f"初始消息数量: {len(state['messages'])}")
    logger.info(f"初始消息状态: {state['message_state']}")
    
    return state


def test_message_addition():
    """
    测试消息添加
    """
    logger.info("测试消息添加...")
    
    # 创建初始状态
    state = create_initial_message_state()
    
    # 创建测试消息
    test_message = {
        "message_id": state["message_id"] + "-test",
        "role": "user",
        "content": "你好，这是一条测试消息",
        "timestamp": "2026-02-27T10:00:00Z",
        "vector": None,  # 向量字段，测试时设为 None
        "status": "processed"
    }
    
    # 添加消息
    new_state = add_message_to_state(state, test_message)
    
    # 验证消息是否添加成功
    assert len(new_state["messages"]) == 1
    assert new_state["messages"][0]["content"] == "你好，这是一条测试消息"
    assert new_state["metrics"]["message_count"] == 1
    assert new_state["metrics"]["user_message_count"] == 1
    
    logger.info("消息添加测试成功")
    return new_state


def test_openai_message_format():
    """
    测试 OpenAI 消息格式
    """
    logger.info("测试 OpenAI 消息格式...")
    
    # 创建符合 OpenAI 格式的消息
    valid_message = create_openai_message("user", "你好，这是一条测试消息")
    assert validate_openai_message_format([valid_message])
    
    # 创建不符合格式的消息
    invalid_message = {"content": "缺少角色"}
    assert not validate_openai_message_format([invalid_message])
    
    logger.info("OpenAI 消息格式测试成功")


def test_openai_message_node_with_doubao():
    """
    测试使用豆包模型的 OpenAI 消息节点
    """
    logger.info("测试使用豆包模型的 OpenAI 消息节点...")
    
    # 检查豆包 API 密钥是否设置
    if not os.getenv("Doubao_API_KEY"):
        logger.warning("Doubao_API_KEY 环境变量未设置，跳过豆包模型测试")
        return
    
    # 创建初始状态
    state = create_initial_message_state()
    
    # 加载角色设定模板
    try:
        character_template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prompt", "character.json")
        loader = PromptTemplateLoader(character_template_path)
        
        # 渲染角色设定模板
        system_prompt = loader.render_template(
            "character_vtuber_1",
            character_name="小初音",
            character_personality="活泼可爱、元气满满",
            character_appearance="蓝绿色双马尾，大眼睛，穿着水手服",
            character_backstory="来自未来的虚拟歌姬，穿越时空来到直播间与粉丝互动",
            streaming_style="轻松愉快，喜欢唱歌和玩游戏"
        )
        
        logger.info("角色设定模板加载成功")
    except Exception as e:
        logger.error(f"加载角色设定模板失败: {e}")
        # 使用默认系统提示词
        system_prompt = "你是一个友好的 AI 助手，用简洁明了的语言回答用户的问题。"
    
    # 添加系统提示词
    state["metadata"]["system_prompt"] = system_prompt
    state["metadata"]["model"] = "doubao-seed-1-8-251228"
    state["metadata"]["temperature"] = 0.7
    state["metadata"]["max_tokens"] = 100
    
    # 添加测试消息
    test_message = {
        "message_id": state["message_id"] + "-test",
        "role": "user",
        "content": "你好，能介绍一下自己吗？",
        "timestamp": "2026-02-27T10:00:00Z",
        "vector": None,  # 向量字段，测试时设为 None
        "status": "processed"
    }
    state = add_message_to_state(state, test_message)
    
    # 调用 OpenAI 消息节点
    result = openai_message_node(state)
    
    # 验证结果
    assert len(result["messages"]) == 2  # 原始消息 + AI 响应
    assert result["messages"][1]["role"] == "assistant"
    assert len(result["messages"][1]["content"]) > 0
    assert result["metrics"]["assistant_message_count"] == 1
    
    logger.info("使用豆包模型的 OpenAI 消息节点测试成功")
    logger.info(f"豆包 AI 响应: {result['messages'][1]['content'][:100]}...")
    
    return result


def test_message_state_graph_with_doubao():
    """
    测试使用豆包模型的消息状态图
    """
    logger.info("测试使用豆包模型的消息状态图...")
    
    # 创建状态图
    graph = StateGraph(MessageState)
    
    # 添加 OpenAI 消息节点
    graph.add_node("openai_message", openai_message_node)
    
    # 设置图结构
    graph.add_edge(START, "openai_message")
    graph.add_edge("openai_message", END)
    
    # 编译图
    app = graph.compile()
    logger.info("消息状态图编译成功")
    
    # 创建测试输入
    test_input = create_initial_message_state()
    
    # 加载角色设定模板
    try:
        character_template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prompt", "character.json")
        loader = PromptTemplateLoader(character_template_path)
        
        # 渲染角色设定模板
        system_prompt = loader.render_template(
            "character_vtuber_1",
            character_name="小初音",
            character_personality="活泼可爱、元气满满",
            character_appearance="蓝绿色双马尾，大眼睛，穿着水手服",
            character_backstory="来自未来的虚拟歌姬，穿越时空来到直播间与粉丝互动",
            streaming_style="轻松愉快，喜欢唱歌和玩游戏"
        )
        
        logger.info("角色设定模板加载成功")
    except Exception as e:
        logger.error(f"加载角色设定模板失败: {e}")
        # 使用默认系统提示词
        system_prompt = "你是一个友好的 AI 助手，用简洁明了的语言回答用户的问题。"
    
    test_input["metadata"]["system_prompt"] = system_prompt
    test_input["metadata"]["model"] = "doubao-seed-1-8-251228"
    test_input["metadata"]["temperature"] = 0.7
    test_input["metadata"]["max_tokens"] = 100
    
    # 添加测试消息
    test_message = {
        "message_id": test_input["message_id"] + "-test",
        "role": "user",
        "content": "你好，能介绍一下自己吗？",
        "timestamp": "2026-02-27T10:00:00Z",
        "vector": None,  # 向量字段，测试时设为 None
        "status": "processed"
    }
    test_input = add_message_to_state(test_input, test_message)
    
    # 运行图
    if os.getenv("Doubao_API_KEY"):
        result = app.invoke(test_input)
        logger.info("使用豆包模型的消息状态图测试成功")
        logger.info(f"图执行结果 - 消息数量: {len(result['messages'])}")
        if len(result['messages']) > 1:
            logger.info(f"豆包 AI 响应: {result['messages'][1]['content'][:100]}...")
    else:
        logger.warning("Doubao_API_KEY 环境变量未设置，跳过图执行测试")
    
    return app


def test_error_handling():
    """
    测试错误处理
    """
    logger.info("测试错误处理...")
    
    # 创建初始状态
    state = create_initial_message_state()
    
    # 移除 API 密钥环境变量（如果存在）
    original_api_key = os.environ.get("Doubao_API_KEY")
    if "Doubao_API_KEY" in os.environ:
        del os.environ["Doubao_API_KEY"]
    
    # 添加测试消息
    test_message = {
        "message_id": state["message_id"] + "-test",
        "role": "user",
        "content": "你好，这是一条测试消息",
        "timestamp": "2026-02-27T10:00:00Z",
        "vector": None,  # 向量字段，测试时设为 None
        "status": "processed"
    }
    state = add_message_to_state(state, test_message)
    
    # 调用 OpenAI 消息节点
    result = openai_message_node(state)
    
    # 验证错误处理
    assert result["error_info"]["error_message"] is not None
    assert "Doubao_API_KEY 未设置" in result["error_info"]["error_message"]
    
    logger.info("错误处理测试成功")
    
    # 恢复原始 API 密钥
    if original_api_key:
        os.environ["Doubao_API_KEY"] = original_api_key
    
    return result


def main():
    """
    主测试函数
    """
    logger.info("=== 开始 OpenAI 消息节点测试 ===")
    
    try:
        # 测试消息状态创建
        test_message_state_creation()
        
        # 测试消息添加
        test_message_addition()
        
        # 测试 OpenAI 消息格式
        test_openai_message_format()
        
        # 测试使用豆包模型的 OpenAI 消息节点
        test_openai_message_node_with_doubao()
        
        # 测试使用豆包模型的消息状态图
        test_message_state_graph_with_doubao()
        
        # 测试错误处理
        test_error_handling()
        
        logger.info("=== 所有测试通过 ===")
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
