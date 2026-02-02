#!/usr/bin/env python3
"""
音频处理模块
提供音频文件验证、信息提取和播放功能
"""

import os
import math
from typing import Dict, Any, Optional
import time
# 尝试导入必要的库
try:
    from pydub import AudioSegment
    try:
        from pydub.playback import play
    except ImportError:
        print("警告: pydub.playback未安装，音频播放功能可能不可用")
        play = None
except ImportError:
    print("警告: pydub库未安装，部分功能可能不可用")
    AudioSegment = None
    play = None

try:
    import librosa
except ImportError:
    print("警告: librosa库未安装，部分功能可能不可用")
    librosa = None

try:
    from mutagen import File
except ImportError:
    print("警告: mutagen库未安装，元数据提取功能可能不可用")
    File = None


def verify_audio_file(file_path: str) -> Dict[str, Any]:
    """
    函数1：验证音频文件是否存在并返回文件格式
    
    Args:
        file_path: 音频文件的路径
    
    Returns:
        Dict[str, Any]: 包含验证结果的字典
            - exists: bool, 文件是否存在
            - format: str, 文件格式（如wav, mp3, flac等）
            - file_name: str, 文件名
            - file_path: str, 文件完整路径
            - error: str, 错误信息（如果有）
    """
    result = {
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "exists": False,
        "format": "",
        "error": None
    }
    
    try:
        # 验证文件是否存在
        if not os.path.exists(file_path):
            result["error"] = "文件不存在"
            return result
        
        result["exists"] = True
        
        # 获取文件格式
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext:
            result["format"] = file_ext[1:]  # 移除点号
        else:
            result["error"] = "无法确定文件格式"
            
    except Exception as e:
        result["error"] = str(e)
        result["exists"] = False
    
    return result



def get_audio_info(file_path: str) -> Dict[str, Any]:
    """
    函数2：获取音频文件的详细信息
    
    Args:
        file_path: 音频文件的路径
    
    Returns:
        Dict[str, Any]: 包含音频信息的字典
            - basic_info: 基本文件信息
            - technical_info: 音频技术参数
            - metadata: 音频元数据
            - analysis_info: 音频分析结果
            - error: 错误信息（如果有）
    """
    # 首先验证文件
    verify_result = verify_audio_file(file_path)
    if not verify_result["exists"]:
        return {
            "success": False,
            "error": verify_result["error"]
        }
    
    audio_info = {
        "success": True,
        "basic_info": {
            "file_path": file_path,
            "file_name": verify_result["file_name"],
            "file_size": os.path.getsize(file_path),
            "format": verify_result["format"],
            "exists": True
        },
        "technical_info": {},
        "metadata": {},
        "analysis_info": {},
        "error": None
    }
    
    try:
        # 使用pydub获取技术参数
        if AudioSegment:
            try:
                audio = AudioSegment.from_file(file_path)
                audio_info["technical_info"] = {
                    "duration": len(audio) / 1000.0,  # 转换为秒
                    "sample_rate": audio.frame_rate,
                    "channels": audio.channels,
                    "bit_depth": audio.sample_width * 8,
                    "frame_width": audio.frame_width,
                    "frame_rate": audio.frame_rate
                }
            except Exception as e:
                audio_info["technical_info"]["error"] = str(e)
        
        # 使用mutagen获取元数据
        if File:
            try:
                metadata = File(file_path, easy=True)
                if metadata:
                    for key, value in metadata.items():
                        if isinstance(value, list) and len(value) > 0:
                            audio_info["metadata"][key] = value[0]
                        else:
                            audio_info["metadata"][key] = value
            except Exception as e:
                audio_info["metadata"]["error"] = str(e)
        
        # 使用librosa进行音频分析
        if librosa:
            try:
                y, sr = librosa.load(file_path, sr=None)
                audio_info["analysis_info"] = {
                    "sample_rate": sr,
                    "duration": librosa.get_duration(y=y, sr=sr),
                    "amplitude_mean": float(y.mean()),
                    "amplitude_max": float(y.max()),
                    "amplitude_min": float(y.min())
                }
                
                # 计算音量
                rms = librosa.feature.rms(y=y)[0]
                audio_info["analysis_info"]["rms"] = float(rms.mean())
                audio_info["analysis_info"]["db_mean"] = float(librosa.amplitude_to_db(rms).mean())
                
                # 计算基频
                f0, voiced_flag, _ = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
                if any(voiced_flag):
                    audio_info["analysis_info"]["f0_mean"] = float(f0[voiced_flag].mean())
                
            except Exception as e:
                audio_info["analysis_info"]["error"] = str(e)
        
    except Exception as e:
        audio_info["success"] = False
        audio_info["error"] = str(e)
    
    return audio_info



def play_audio(file_path: str, duration: Optional[float] = None) -> Dict[str, Any]:
    """
    函数3：播放音频文件，可选择播放时长
    
    Args:
        file_path: 音频文件的路径
        duration: 播放时长（秒），None表示播放完整音频
    
    Returns:
        Dict[str, Any]: 包含播放结果的字典
            - success: bool, 播放是否成功
            - played_duration: float, 实际播放时长（秒）
            - total_duration: float, 音频总时长（秒）
            - error: str, 错误信息（如果有）
    """
    # 首先验证文件
    verify_result = verify_audio_file(file_path)
    if not verify_result["exists"]:
        return {
            "success": False,
            "error": verify_result["error"]
        }
    
    if AudioSegment is None:
        return {
            "success": False,
            "error": "pydub库未安装，无法播放音频"
        }
    
    if play is None:
        return {
            "success": False,
            "error": "无法播放音频：缺少必要的播放组件"
        }
    
    play_result = {
        "success": False,
        "played_duration": 0.0,
        "total_duration": 0.0,
        "error": None
    }
    
    try:
        # 加载音频
        audio = AudioSegment.from_file(file_path)
        total_duration = len(audio) / 1000.0
        play_result["total_duration"] = total_duration
        
        # 确定播放时长
        play_duration = duration if duration and duration > 0 else total_duration
        play_duration = min(play_duration, total_duration)  # 不超过总时长
        
        # 截取需要播放的部分
        if play_duration < total_duration:
            audio_to_play = audio[:int(play_duration * 1000)]
        else:
            audio_to_play = audio
        
        # 记录开始时间
        start_time = time.time()
        
        # 播放音频
        print(f"开始播放音频: {verify_result['file_name']}")
        print(f"总时长: {format_duration(total_duration)}")
        if duration:
            print(f"计划播放时长: {format_duration(play_duration)}")
        
        play(audio_to_play)
        
        # 记录结束时间
        end_time = time.time()
        actual_duration = end_time - start_time
        play_result["played_duration"] = actual_duration
        play_result["success"] = True
        
        print(f"播放完成，实际播放时长: {format_duration(actual_duration)}")
        
    except Exception as e:
        play_result["error"] = str(e)
    
    return play_result


# 辅助函数
def format_duration(seconds: float) -> str:
    """
    将时长（秒）格式化为可读的字符串
    
    Args:
        seconds: 时长（秒）
    
    Returns:
        str: 格式化后的时长字符串（如 "2:34" 或 "1:02:34"）
    """
    if seconds < 0:
        return "0:00"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    else:
        return f"{minutes:02d}:{secs:05.2f}"


def format_size(size_bytes: int) -> str:
    """
    将字节大小格式化为可读的字符串
    
    Args:
        size_bytes: 字节大小
    
    Returns:
        str: 格式化后的大小字符串（如 "1.23 MB"）
    """
    if size_bytes == 0:
        return "0 B"
    
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    
    return f"{s} {size_name[i]}"


def print_audio_info(audio_info: Dict[str, Any]) -> None:
    """
    打印音频信息
    
    Args:
        audio_info: 音频信息字典
    """
    if not audio_info.get("success", False):
        print(f"获取音频信息失败: {audio_info.get('error', '未知错误')}")
        return
    
    print("=== 音频文件信息 ===")
    
    # 基本信息
    basic = audio_info["basic_info"]
    print(f"文件名: {basic['file_name']}")
    print(f"文件路径: {basic['file_path']}")
    print(f"文件大小: {format_size(basic['file_size'])}")
    print(f"文件格式: {basic['format']}")
    
    # 技术参数
    tech = audio_info["technical_info"]
    if tech and "error" not in tech:
        print("\n=== 技术参数 ===")
        if "duration" in tech:
            print(f"时长: {format_duration(tech['duration'])}")
        if "sample_rate" in tech:
            print(f"采样率: {tech['sample_rate']} Hz")
        if "channels" in tech:
            print(f"声道数: {tech['channels']} ({'单声道' if tech['channels'] == 1 else '立体声'})")
        if "bit_depth" in tech:
            print(f"位深度: {tech['bit_depth']} bit")
    
    # 元数据
    metadata = audio_info["metadata"]
    if metadata and "error" not in metadata and metadata:
        print("\n=== 元数据 ===")
        for key, value in metadata.items():
            print(f"{key}: {value}")
    
    # 分析信息
    analysis = audio_info["analysis_info"]
    if analysis and "error" not in analysis:
        print("\n=== 音频分析 ===")
        if "db_mean" in analysis:
            print(f"平均音量: {analysis['db_mean']:.2f} dB")
        if "f0_mean" in analysis:
            print(f"平均基频: {analysis['f0_mean']:.2f} Hz")


# 示例用法
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python audio_processing.py <音频文件路径> [播放时长(秒)]")
        print("\n示例:")
        print("  python audio_processing.py audio.mp3          # 完整播放")
        print("  python audio_processing.py audio.mp3 10       # 播放10秒")
        sys.exit(1)
    
    audio_path = sys.argv[1]
    play_duration = float(sys.argv[2]) if len(sys.argv) > 2 else None
    
    print("=== 函数1：验证音频文件 ===")
    verify_result = verify_audio_file(audio_path)
    print(verify_result)
    
    if verify_result["exists"]:
        print("\n=== 函数2：获取音频信息 ===")
        audio_info = get_audio_info(audio_path)
        print_audio_info(audio_info)
        
        print("\n=== 函数3：播放音频 ===")
        play_result = play_audio(audio_path, play_duration)
        print(play_result)
    else:
        print(f"\n无法继续处理，因为文件不存在: {audio_path}")
