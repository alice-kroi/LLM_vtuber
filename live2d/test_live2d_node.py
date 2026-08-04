#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 节点测试程序
测试 live2d_node 函数的输入输出
"""

import sys
import os

# 添加项目根目录到sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d.live2d_main import live2d_node, live2d_controller

def test_live2d_node_with_visual_focus():
    """测试视觉焦点输入"""
    print("=== 测试视觉焦点输入 ===")
    
    test_cases = [
        {"visual_focus": "center", "action": ""},
        {"visual_focus": "up", "action": ""},
        {"visual_focus": "down", "action": ""},
        {"visual_focus": "left", "action": ""},
        {"visual_focus": "right", "action": ""},
        {"visual_focus": "upleft", "action": ""},
        {"visual_focus": "upright", "action": ""},
        {"visual_focus": "downleft", "action": ""},
        {"visual_focus": "downright", "action": ""},
    ]
    
    for i, test_case in enumerate(test_cases):
        print(f"\n测试 {i+1}: {test_case}")
        result = live2d_node(test_case)
        print(f"输出: live2d_status={result.get('live2d_status')}, live2d_message={result.get('live2d_message')}")

def test_live2d_node_with_action():
    """测试动作输入"""
    print("\n=== 测试动作输入 ===")
    
    test_cases = [
        {"visual_focus": "", "action": "open_mouth"},
        {"visual_focus": "", "action": "close_mouth"},
        {"visual_focus": "", "action": "idle"},
    ]
    
    for i, test_case in enumerate(test_cases):
        print(f"\n测试 {i+1}: {test_case}")
        result = live2d_node(test_case)
        print(f"输出: live2d_status={result.get('live2d_status')}, live2d_message={result.get('live2d_message')}")

def test_live2d_node_with_both():
    """测试同时输入视觉焦点和动作"""
    print("\n=== 测试视觉焦点+动作组合 ===")
    
    test_cases = [
        {"visual_focus": "center", "action": "open_mouth"},
        {"visual_focus": "up", "action": "close_mouth"},
    ]
    
    for i, test_case in enumerate(test_cases):
        print(f"\n测试 {i+1}: {test_case}")
        result = live2d_node(test_case)
        print(f"输出: live2d_status={result.get('live2d_status')}, live2d_message={result.get('live2d_message')}")

def test_live2d_node_with_response():
    """测试通过response自动提取语气"""
    print("\n=== 测试通过response自动提取语气 ===")
    
    test_cases = [
        {"response": "【开心】今天天气真好！"},
        {"response": "【生气】你怎么这样！"},
        {"response": "【调皮】要不要猜猜我在想什么？"},
        {"response": "【疑问】你是谁呀？"},
    ]
    
    for i, test_case in enumerate(test_cases):
        print(f"\n测试 {i+1}: response={test_case['response']}")
        result = live2d_node(test_case)
        print(f"输出: live2d_status={result.get('live2d_status')}, live2d_message={result.get('live2d_message')}")

def test_live2d_node_empty_input():
    """测试空输入"""
    print("\n=== 测试空输入 ===")
    
    test_case = {"visual_focus": "", "action": ""}
    print(f"测试: {test_case}")
    result = live2d_node(test_case)
    print(f"输出: live2d_status={result.get('live2d_status')}, live2d_message={result.get('live2d_message')}")

def main():
    """主测试函数"""
    print("=== Live2D 节点测试程序 ===")
    print("注意：请确保 Live2D 控制器已在另一个终端启动")
    
    # 检查控制器是否已启动
    if not live2d_controller or not live2d_controller.running:
        print("警告：Live2D控制器未启动，请先运行 live2d_main.py")
        print("测试将模拟执行（不会实际发送指令）")
    
    # 运行所有测试
    test_live2d_node_with_visual_focus()
    test_live2d_node_with_action()
    test_live2d_node_with_both()
    test_live2d_node_with_response()
    test_live2d_node_empty_input()
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n测试被用户中断")
    except Exception as e:
        print(f"测试异常: {e}")
        import traceback
        traceback.print_exc()