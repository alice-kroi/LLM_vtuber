#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单测试提示词加载功能
"""

import json
import os

# 测试函数
def test_prompt_load():
    print("=== 开始测试提示词加载 ===")
    
    # 读取 character.json 文件
    try:
        file_path = "prompt\character.json"
        print(f"读取文件: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print("文件读取成功")
        print(f"模板ID: {data.get('template_id')}")
        print(f"模板名称: {data.get('name')}")
        print(f"变量列表: {data.get('variables')}")
        
        # 测试模板渲染
        content = data.get('content')
        print(f"原始模板: {content[:100]}...")
        
        # 替换变量
        rendered = content.replace("{character_name}", "小初音")
        rendered = rendered.replace("{character_personality}", "活泼可爱、元气满满")
        rendered = rendered.replace("{character_appearance}", "蓝绿色双马尾，大眼睛，穿着水手服")
        rendered = rendered.replace("{character_backstory}", "来自未来的虚拟歌姬，穿越时空来到直播间与粉丝互动")
        rendered = rendered.replace("{streaming_style}", "轻松愉快，喜欢唱歌和玩游戏")
        
        print(f"渲染后: {rendered}")
        print("=== 测试成功 ===")
        
    except Exception as e:
        print(f"测试失败: {e}")

if __name__ == "__main__":
    test_prompt_load()
