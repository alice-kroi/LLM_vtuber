#!/usr/bin/env python3
"""
集成测试：测试整个 LangGraph 流程，包括 TTS 节点
"""

import sys
import os
import asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def test_full_graph_with_tts():
    """测试完整的图流程，包括 TTS 节点"""
    print("=== 测试完整 LangGraph 流程（含 TTS）===")
    
    # 设置全局参数
    import argparse
    args = argparse.Namespace()
    args.tts = True
    args.live2d = False
    args.room_id = None
    
    import main
    main.args = args
    
    # 创建管理器
    manager = main.LangGraphManager()
    
    # 创建测试状态（使用 LLMState）
    from LLM_node import LLMState
    
    test_messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "【开心】大家好呀！今天天气真好呢~"}
    ]
    
    initial_state = LLMState(
        messages=test_messages,
        response="【开心】大家好呀！今天天气真好呢~",
        system_prompt="测试提示词",
        context=None
    )
    
    print(f"\n初始状态:")
    print(f"  messages: {[m['content'] for m in test_messages]}")
    print(f"  response: {initial_state['response']}")
    
    # 运行图
    print("\n=== 开始执行图 ===")
    try:
        result = await manager.run_with_messages(initial_state=initial_state)
        
        print(f"\n执行结果:")
        print(f"  response: {result.get('response', '')}")
        print(f"  tts_played: {result.get('tts_played', False)}")
        print(f"  tts_duration: {result.get('tts_duration', 0)}")
        print(f"  tts_tone: {result.get('tts_tone', '')}")
        print(f"  tts_error: {result.get('tts_error', 'None')}")
        
    except Exception as e:
        print(f"执行失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_full_graph_with_tts())
