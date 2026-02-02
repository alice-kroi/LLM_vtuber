#!/usr/bin/env python3
"""
音频处理主程序
提供音频文件的信息获取和播放功能
"""

import os
import argparse
import sys
from typing import Optional

# 导入音频处理模块
try:
    from audio_deal import (
        verify_audio_file,
        get_audio_info,
        play_audio,
        print_audio_info,
        format_duration
    )
except ImportError:
    print("错误: 无法导入音频处理模块，请确保audio_processing.py存在于同一目录中")
    sys.exit(1)


def main():
    """
    主程序入口
    """
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="音频处理主程序 - 提供音频信息获取和播放功能")
    parser.add_argument("audio_path", nargs="?", help="音频文件路径")
    parser.add_argument("--play", action="store_true", help="播放音频")
    parser.add_argument("--duration", type=float, help="播放时长（秒）")
    parser.add_argument("--info-only", action="store_true", help="仅显示音频信息，不播放")
    
    args = parser.parse_args()
    
    # 如果没有提供音频路径，进入交互式模式
    if not args.audio_path:
        audio_path = get_audio_path_from_user()
        if not audio_path:
            print("未提供有效音频文件路径，程序退出")
            sys.exit(1)
    else:
        audio_path = args.audio_path
    
    # 验证音频文件
    print("正在验证音频文件...")
    verify_result = verify_audio_file(audio_path)
    
    if not verify_result["exists"]:
        print(f"错误: {verify_result['error']}")
        sys.exit(1)
    
    print(f"音频文件验证成功: {verify_result['file_name']} ({verify_result['format']})")
    
    # 获取并显示音频信息
    print("\n正在获取音频信息...")
    audio_info = get_audio_info(audio_path)
    
    if not audio_info["success"]:
        print(f"获取音频信息失败: {audio_info['error']}")
    else:
        print_audio_info(audio_info)
    
    # 播放音频
    if not args.info_only and (args.play or not args.audio_path):
        play_audio_file(audio_path, args.duration)
    elif args.info_only:
        print("\n已选择仅显示信息模式，跳过播放")


def get_audio_path_from_user() -> Optional[str]:
    """
    从用户输入获取音频文件路径
    
    Returns:
        Optional[str]: 用户输入的音频文件路径，如果取消则返回None
    """
    print("=== 音频处理主程序 ===")
    print("请输入音频文件的路径，或输入'q'取消")
    
    while True:
        path = input("音频文件路径: ").strip()
        
        if path.lower() in ['q', 'quit', 'exit']:
            return None
            
        if not path:
            print("错误: 路径不能为空，请重新输入")
            continue
            
        # 处理相对路径
        if not os.path.isabs(path):
            path = os.path.abspath(path)
            
        return path


def play_audio_file(audio_path: str, duration: Optional[float] = None) -> None:
    """
    播放音频文件
    
    Args:
        audio_path: 音频文件路径
        duration: 播放时长（秒）
    """
    print("\n=== 音频播放 ===")
    
    # 如果没有指定时长，询问用户是否要指定
    if duration is None:
        user_choice = input("是否要指定播放时长？(y/n，默认为n): ").strip().lower()
        if user_choice == 'y':
            try:
                duration_input = input("请输入播放时长（秒）: ").strip()
                duration = float(duration_input)
                if duration <= 0:
                    print("播放时长必须大于0，将播放完整音频")
                    duration = None
            except ValueError:
                print("无效的时长输入，将播放完整音频")
                duration = None
    
    # 执行播放
    print("正在准备播放...")
    play_result = play_audio(audio_path, duration)
    
    if play_result["success"]:
        print("\n=== 播放结果 ===")
        print(f"音频总时长: {format_duration(play_result['total_duration'])}")
        print(f"实际播放时长: {format_duration(play_result['played_duration'])}")
        print("播放完成！")
    else:
        print(f"\n播放失败: {play_result['error']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序发生错误: {e}")
        sys.exit(1)
