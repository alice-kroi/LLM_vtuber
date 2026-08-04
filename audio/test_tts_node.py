#!/usr/bin/env python3
"""
测试 tts_node 函数
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from audio_main import tts_node

def test_tts_node():
    """测试 tts_node 函数"""
    print("=== 测试 tts_node 函数 ===")
    
    # 测试用例1: 带语气的文本
    test_state1 = {
        "response": "【撩拨】感觉怎么样？我是不是比你想象中，还要再腼腆一些？"
    }
    print("\n测试1: 带语气的文本")
    print(f"输入状态: {test_state1}")
    result = tts_node(test_state1)
    print(f"输出: {result} (预期: None)")
    
    # 测试用例2: 不带语气的文本
    test_state2 = {
        "response": "今天天气真好"
    }
    print("\n测试2: 不带语气的文本")
    print(f"输入状态: {test_state2}")
    result = tts_node(test_state2)
    print(f"输出: {result} (预期: None)")
    
    # 测试用例3: 空文本
    test_state3 = {
        "response": ""
    }
    print("\n测试3: 空文本")
    print(f"输入状态: {test_state3}")
    result = tts_node(test_state3)
    print(f"输出: {result} (预期: None)")

if __name__ == "__main__":
    import os
    test_tts_node()