#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单测试脚本，用于验证提示词加载和豆包模型调用
"""

import logging
import os
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from message_state import MessageState, create_initial_message_state, add_message_to_state
from openai_message_node import openai_message_node
from prompt.prompt_load import PromptTemplateLoader

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_simple():
    """
    简单测试函数
    """
    logger.info("=== 开始简单测试 ===")
    
    # 检查豆包 API 密钥是否设置
    if not os.getenv("Doubao_API_KEY"):
        logger.warning("Doubao_API_KEY 环境变量未设置，跳过测试")
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
        logger.info(f"渲染后的系统提示词: {system_prompt[:100]}...")
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
    logger.info("调用 OpenAI 消息节点...")
    result = openai_message_node(state)
    
    # 验证结果
    assert len(result["messages"]) == 2  # 原始消息 + AI 响应
    assert result["messages"][1]["role"] == "assistant"
    assert len(result["messages"][1]["content"]) > 0
    
    logger.info("测试成功！")
    logger.info(f"豆包 AI 响应: {result['messages'][1]['content']}")
    logger.info("=== 测试完成 ===")

if __name__ == "__main__":
    test_simple()
