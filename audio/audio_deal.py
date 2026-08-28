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

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    print("警告: sounddevice或numpy库未安装，音频虚拟输入功能可能不可用")
    sd = None
    np = None


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


def audio_as_microphone(file_path: str, output_device: Optional[int] = None) -> Dict[str, Any]:
    """
    函数4：将音频文件作为计算机麦克风的输入
    
    Args:
        file_path: 音频文件的路径
        output_device: 输出设备编号，None表示使用默认设备
    
    Returns:
        Dict[str, Any]: 包含操作结果的字典
            - success: bool, 操作是否成功
            - error: str, 错误信息（如果有）
            - duration: float, 音频时长（秒）
            - output_device: int, 使用的输出设备编号
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
            "error": "pydub库未安装，无法处理音频"
        }
    
    if sd is None or np is None:
        return {
            "success": False,
            "error": "sounddevice或numpy库未安装，无法模拟麦克风输入"
        }
    
    result = {
        "success": False,
        "error": None,
        "duration": 0.0,
        "output_device": output_device
    }
    
    try:
        # 加载音频文件
        print(f"正在加载音频文件: {verify_result['file_name']}")
        audio = AudioSegment.from_file(file_path)
        
        # 转换为适合sounddevice的格式
        sample_rate = audio.frame_rate
        channels = audio.channels
        
        # 将音频数据转换为numpy数组
        samples = np.array(audio.get_array_of_samples())
        
        # 归一化到[-1, 1]范围
        if audio.sample_width == 2:
            samples = samples.astype(np.float32) / 32768.0
        elif audio.sample_width == 4:
            samples = samples.astype(np.float32) / 2147483648.0
        
        # 如果是立体声，转换为单声道
        if channels == 2:
            samples = np.mean(samples.reshape(-1, 2), axis=1)
        
        duration = len(audio) / 1000.0
        result["duration"] = duration
        
        # 获取输出设备信息
        if output_device is None:
            output_device = sd.default.device[1]
            device_info = "默认设备"
        else:
            try:
                device_info = sd.query_devices(output_device)['name']
            except Exception:
                device_info = f"设备 {output_device}"
        
        result["output_device"] = output_device
        
        print(f"音频文件加载完成，时长: {format_duration(duration)}")
        print(f"采样率: {sample_rate} Hz")
        print(f"输出设备: [{output_device}] {device_info}")
        print("开始将音频作为麦克风输入...")
        
        # 播放音频到指定输出设备（模拟麦克风输入）
        # 注意：这实际上是播放音频，而不是真正的麦克风输入
        # 要实现真正的虚拟麦克风输入，需要使用虚拟音频设备
        sd.play(samples, samplerate=sample_rate, device=output_device)
        
        # 等待播放完成
        sd.wait()
        
        print("音频播放完成，已作为麦克风输入")
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def get_audio_input_devices() -> Dict[str, Any]:
    """
    函数5：获取本地设备的声音输入设备列表
    
    Returns:
        Dict[str, Any]: 包含输入设备列表的字典
            - success: bool, 操作是否成功
            - devices: dict, 设备编号到设备名称的映射
            - default_device: int, 默认输入设备编号（如果有）
            - error: str, 错误信息（如果有）
    """
    if sd is None:
        return {
            "success": False,
            "error": "sounddevice库未安装，无法获取设备列表"
        }
    
    result = {
        "success": False,
        "devices": {},
        "default_device": None,
        "error": None
    }
    
    try:
        # 获取所有音频设备
        devices = sd.query_devices()
        
        # 筛选输入设备
        input_devices = {}
        default_input = sd.default.device[0]
        
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                input_devices[i] = device['name']
        
        result["devices"] = input_devices
        result["default_device"] = default_input
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def get_audio_output_devices() -> Dict[str, Any]:
    """
    函数6：获取本地设备的声音输出设备列表
    
    Returns:
        Dict[str, Any]: 包含输出设备列表的字典
            - success: bool, 操作是否成功
            - devices: dict, 设备编号到设备名称的映射
            - default_device: int, 默认输出设备编号（如果有）
            - error: str, 错误信息（如果有）
    """
    if sd is None:
        return {
            "success": False,
            "error": "sounddevice库未安装，无法获取设备列表"
        }
    
    result = {
        "success": False,
        "devices": {},
        "default_device": None,
        "error": None
    }
    
    try:
        devices = sd.query_devices()
        
        output_devices = {}
        default_output = sd.default.device[1]
        
        for i, device in enumerate(devices):
            if device['max_output_channels'] > 0:
                output_devices[i] = device['name']
        
        result["devices"] = output_devices
        result["default_device"] = default_output
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


def play_audio_stream(audio_data: bytes, output_device: Optional[int] = None, 
                      sample_rate: Optional[int] = None) -> Dict[str, Any]:
    """
    函数7：直接播放音频流数据（不保存到文件）
    
    Args:
        audio_data: 音频数据（支持 WAV 格式或原始 PCM 数据）
        output_device: 输出设备编号，None表示使用默认设备
        sample_rate: 采样率，用于纯 PCM 数据。如果是 WAV 格式会自动解析
    
    Returns:
        Dict[str, Any]: 包含播放结果的字典
            - success: bool, 播放是否成功
            - error: str, 错误信息（如果有）
            - duration: float, 音频时长（秒）
            - sample_rate: int, 采样率
    """
    result = {
        "success": False,
        "error": None,
        "duration": 0.0,
        "sample_rate": sample_rate or 32000
    }
    
    if sd is None or np is None:
        result["error"] = "sounddevice或numpy库未安装，无法播放音频流"
        return result
    
    if not audio_data or len(audio_data) == 0:
        result["error"] = "音频数据为空"
        return result
    
    try:
        if audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE':
            sample_rate = int.from_bytes(audio_data[24:28], 'little')
            bits_per_sample = int.from_bytes(audio_data[34:36], 'little')
            num_channels = int.from_bytes(audio_data[22:24], 'little')
            
            print(f"解析WAV头: 采样率={sample_rate} Hz, 位深度={bits_per_sample} bit, 声道数={num_channels}")
            
            data_start = 12
            while data_start < len(audio_data) - 8:
                chunk_id = audio_data[data_start:data_start+4]
                chunk_size = int.from_bytes(audio_data[data_start+4:data_start+8], 'little')
                if chunk_id == b'data':
                    data_start += 8
                    break
                data_start += 8 + chunk_size
            else:
                result["error"] = "无法找到音频数据块"
                return result
            
            raw_data = audio_data[data_start:]
            print(f"原始数据大小: {len(raw_data)} bytes")
            
            if bits_per_sample == 16:
                samples = np.frombuffer(raw_data, dtype=np.int16)
            elif bits_per_sample == 32:
                samples = np.frombuffer(raw_data, dtype=np.int32)
            else:
                print(f"警告: 不支持的位深度 {bits_per_sample}，使用16位")
                samples = np.frombuffer(raw_data, dtype=np.int16)
        else:
            if sample_rate is None:
                sample_rate = 32000
            print(f"解析PCM数据: 采样率={sample_rate} Hz (16位)")
            samples = np.frombuffer(audio_data, dtype=np.int16)
        
        print(f"采样数: {len(samples)}, 预期时长: {len(samples) / sample_rate:.2f} 秒")
        
        samples = samples.astype(np.float32) / 32768.0
        
        print(f"归一化后采样数: {len(samples)}")
        
        duration = len(samples) / sample_rate
        result["duration"] = duration
        result["sample_rate"] = sample_rate
        
        if output_device is None:
            output_device = sd.default.device[1]
            device_info = "默认设备"
        else:
            try:
                device_info = sd.query_devices(output_device)['name']
            except Exception:
                device_info = f"设备 {output_device}"
        
        print(f"音频流加载完成，时长: {format_duration(duration)}")
        print(f"采样率: {sample_rate} Hz")
        print(f"输出设备: [{output_device}] {device_info}")
        print("开始播放音频流...")
        
        sd.play(samples, samplerate=sample_rate, device=output_device)
        sd.wait()
        
        print("音频流播放完成")
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"播放音频流异常: {str(e)}"
    
    return result


def play_audio_stream_chunks(chunks_generator, sample_rate: int = 32000, output_device: Optional[int] = None) -> Dict[str, Any]:
    """
    函数8：流式播放音频块（边接收边播放）
    
    Args:
        chunks_generator: 音频块生成器，每块为 bytes 数据
        sample_rate: 采样率
        output_device: 输出设备编号，None表示使用默认设备
    
    Returns:
        Dict[str, Any]: 包含播放结果的字典
            - success: bool, 播放是否成功
            - error: str, 错误信息（如果有）
            - total_duration: float, 总音频时长（秒）
            - chunks_received: int, 接收的块数量
    """
    result = {
        "success": False,
        "error": None,
        "total_duration": 0.0,
        "chunks_received": 0
    }
    
    if sd is None or np is None:
        result["error"] = "sounddevice或numpy库未安装，无法播放音频流"
        return result
    
    try:
        all_samples = []
        
        for chunk in chunks_generator:
            if not chunk or len(chunk) == 0:
                continue
            
            result["chunks_received"] += 1
            
            if chunk[:4] == b'RIFF':
                data_start = 12
                chunk_data = chunk
                while data_start < len(chunk_data) - 8:
                    chunk_id = chunk_data[data_start:data_start+4]
                    chunk_size = int.from_bytes(chunk_data[data_start+4:data_start+8], 'little')
                    if chunk_id == b'data':
                        data_start += 8
                        break
                    data_start += 8 + chunk_size
                else:
                    data_start = 44 if len(chunk_data) > 44 else len(chunk_data)
                
                if data_start < len(chunk_data):
                    samples = np.frombuffer(chunk_data[data_start:], dtype=np.int16)
                else:
                    samples = np.array([], dtype=np.int16)
            else:
                samples = np.frombuffer(chunk, dtype=np.int16)
            
            if len(samples) > 0:
                all_samples.append(samples)
        
        if not all_samples:
            result["error"] = "未接收到任何有效音频数据"
            return result
        
        combined_samples = np.concatenate(all_samples)
        combined_samples = combined_samples.astype(np.float32) / 32768.0
        
        if len(combined_samples) > 1 and len(combined_samples) % 2 == 0:
            combined_samples = np.mean(combined_samples.reshape(-1, 2), axis=1)
        
        duration = len(combined_samples) / sample_rate
        result["total_duration"] = duration
        
        if output_device is None:
            output_device = sd.default.device[1]
            device_info = "默认设备"
        else:
            try:
                device_info = sd.query_devices(output_device)['name']
            except Exception:
                device_info = f"设备 {output_device}"
        
        print(f"音频流接收完成，总时长: {format_duration(duration)}")
        print(f"采样率: {sample_rate} Hz")
        print(f"接收块数: {result['chunks_received']}")
        print(f"输出设备: [{output_device}] {device_info}")
        print("开始播放音频...")
        
        sd.play(combined_samples, samplerate=sample_rate, device=output_device)
        sd.wait()
        
        print("音频播放完成")
        result["success"] = True
        
    except Exception as e:
        result["error"] = f"流式播放音频异常: {str(e)}"
    
    return result
