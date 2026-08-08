#!/usr/bin/env python3
"""
调试 TTS 节点，检查请求是否发送成功
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_tts_node_with_state():
    """测试 TTS 节点是否正确处理状态"""
    print("=== 测试 TTS 节点 ===")
    
    # 模拟状态
    test_state = {
        "response": "【开心】大家好呀！今天天气真好呢~",
        "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "【开心】大家好呀！今天天气真好呢~"}
        ]
    }
    
    print(f"输入状态:")
    print(f"  response: {test_state['response']}")
    
    # 导入 TTS 节点
    from audio.audio_main import tts_node, parse_text_with_tone, find_ref_audio_by_tone
    
    # 先测试解析逻辑
    text = test_state.get("response", "")
    tone, content = parse_text_with_tone(text)
    print(f"\n解析结果:")
    print(f"  语气: '{tone}'")
    print(f"  内容: '{content}'")
    
    # 测试查找参考音频
    if tone:
        found_audio = find_ref_audio_by_tone(tone)
        print(f"  参考音频: {found_audio}")
    
    # 调用 TTS 节点
    print("\n调用 TTS 节点...")
    result = tts_node(test_state)
    
    print("\n输出状态:")
    print(f"  tts_played: {result.get('tts_played', False)}")
    print(f"  tts_duration: {result.get('tts_duration', 0)}")
    print(f"  tts_tone: {result.get('tts_tone', '')}")
    print(f"  tts_error: {result.get('tts_error', 'None')}")

if __name__ == "__main__":
    test_tts_node_with_state()
