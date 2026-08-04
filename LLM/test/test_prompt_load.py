#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试提示词加载功能
"""

import logging
import os
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from prompt.prompt_load import PromptTemplateLoader

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_prompt_load():
    """
    测试提示词加载功能
    """
    logger.info("=== 开始提示词加载测试 ===")
    
    # 加载角色设定模板
    try:
        character_template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prompt", "character.json")
        logger.info(f"加载模板文件: {character_template_path}")
        
        loader = PromptTemplateLoader(character_template_path)
        logger.info("模板加载器创建成功")
        
        # 列出可用模板
        templates = loader.list_templates()
        logger.info(f"可用模板: {templates}")
        
        # 渲染角色设定模板
        system_prompt = loader.render_template(
            "character_vtuber_1",
            character_name="小初音",
            character_personality="活泼可爱、元气满满",
            character_appearance="蓝绿色双马尾，大眼睛，穿着水手服",
            character_backstory="来自未来的虚拟歌姬，穿越时空来到直播间与粉丝互动",
            streaming_style="轻松愉快，喜欢唱歌和玩游戏"
        )
        
        logger.info("角色设定模板渲染成功")
        logger.info(f"渲染后的系统提示词: {system_prompt}")
        
        logger.info("=== 提示词加载测试成功 ===")
        return True
        
    except Exception as e:
        logger.error(f"加载角色设定模板失败: {e}")
        logger.info("=== 提示词加载测试失败 ===")
        return False

if __name__ == "__main__":
    test_prompt_load()