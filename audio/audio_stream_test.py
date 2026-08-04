#!/usr/bin/env python3
"""
流式TTS音频播放测试
测试接收TTS服务的音频流并直接播放
"""

import os
import sys
import argparse
import asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from audio_main import tts_request_and_stream_play, parse_text_with_tone, find_ref_audio_by_tone

REF_AUDIO_PATH = r"D:\Github\【GPT-SoVITS】爱莉希雅V2\参考音频"

def parse_args():
    parser = argparse.ArgumentParser(description="流式TTS音频播放测试")
    parser.add_argument("-t", "--text", type=str, default="啊要不要下次换上凯文的外套来试试他的反应呢欸嘿", help="要合成的文本")
    parser.add_argument("-p", "--prompt_text", type=str, default="", help="提示文本/语气（不指定则从参考音频文件名提取）")
    parser.add_argument("-r", "--ref_audio_path", type=str, default="", help="参考音频路径")
    parser.add_argument("-u", "--url", type=str, default="http://127.0.0.1:9880/tts", help="TTS服务地址")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="TTS服务主机")
    parser.add_argument("--port", type=int, default=9880, help="TTS服务端口")
    parser.add_argument("--tone", type=str, default="", help="语气（将自动查找对应参考音频）")
    parser.add_argument("--speed", type=float, default=1.0, help="音频速度因子，小于1减速，大于1加速，默认1.0")
    parser.add_argument("--streaming", type=int, default=2, help="流式模式: 0=禁用(非流式), 1=高质量, 2=中等质量, 3=快速")
    parser.add_argument("--save", type=str, default="", help="保存音频到文件路径")
    return parser.parse_args()

def main():
    args = parse_args()
    
    print("=== 流式TTS音频播放测试 ===")
    
    prompt_text = args.prompt_text
    ref_audio_path = args.ref_audio_path
    
    tone, content = parse_text_with_tone(args.text)
    
    print(f"原文: {args.text}")
    if tone:
        print(f"语气: '{tone}'")
    print(f"内容: '{content}'")
    print(f"音频速度: {args.speed}")
    print(f"流式模式: {args.streaming} ({'禁用' if args.streaming == 0 else '启用'})")
    
    if args.tone:
        prompt_text = args.tone
        found_audio = find_ref_audio_by_tone(args.tone, REF_AUDIO_PATH)
        if found_audio:
            ref_audio_path = found_audio
            print(f"根据语气'{args.tone}'找到参考音频: {os.path.basename(found_audio)}")
        else:
            print(f"警告: 未找到语气'{args.tone}'对应的参考音频")
    
    if tone and not ref_audio_path:
        found_audio = find_ref_audio_by_tone(tone, REF_AUDIO_PATH)
        if found_audio:
            ref_audio_path = found_audio
            print(f"根据语气'{tone}'找到参考音频: {os.path.basename(found_audio)}")
        else:
            print(f"警告: 未找到语气'{tone}'对应的参考音频")
    
    if ref_audio_path:
        print(f"参考音频: {os.path.basename(ref_audio_path)}")
        
        filename = os.path.basename(ref_audio_path)
        name_without_ext = os.path.splitext(filename)[0]
        if name_without_ext.startswith("【"):
            end_bracket = name_without_ext.find("】")
            if end_bracket != -1:
                prompt_text = name_without_ext[end_bracket+1:]
                print(f"从参考音频文件名提取提示文本: {prompt_text}")
            else:
                prompt_text = name_without_ext
        else:
            prompt_text = name_without_ext
    else:
        print("警告: 未指定参考音频")
    
    if prompt_text:
        print(f"提示文本: '{prompt_text}'")
    
    print()
    print("发送 TTS 请求并直接播放...")
    print(f"TTS请求参数: {{\"text\": \"{content}\", \"prompt_text\": \"{prompt_text}\", \"ref_audio_path\": \"{ref_audio_path}\", \"speed_factor\": {args.speed}, \"streaming_mode\": {args.streaming}}}")
    print()
    
    try:
        result = asyncio.run(tts_request_and_stream_play(
            text=content,
            prompt_text=prompt_text,
            ref_audio_path=ref_audio_path,
            host=args.host,
            port=args.port,
            speed_factor=args.speed,
            streaming_mode=args.streaming,
            save_path=args.save
        ))
        
        print()
        if result["success"]:
            print("=== 测试结果 ===")
            print(f"状态: 成功")
            print(f"音频时长: {result['audio_duration']:.2f} 秒")
        else:
            print("=== 测试结果 ===")
            print(f"状态: 失败")
            print(f"错误信息: {result['error']}")
            return 1
            
    except Exception as e:
        print(f"测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序发生错误: {e}")
        sys.exit(1)