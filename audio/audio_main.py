#!/usr/bin/env python3
"""
音频处理主程序
提供音频文件的信息获取、播放功能，支持与主程序集成
包含 TTS 功能和 langraph 节点支持
"""

import os
import argparse
import sys
import json
import asyncio
from typing import Optional, Dict, Any

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from audio_deal import (
    verify_audio_file,
    get_audio_info,
    play_audio,
    play_audio_stream,
    print_audio_info,
    format_duration,
    audio_as_microphone,
    get_audio_input_devices,
    get_audio_output_devices
)

args = None

TTS_HOST = "127.0.0.1"
TTS_PORT = 9880
REF_AUDIO_PATH = r"D:\Github\【GPT-SoVITS】爱莉希雅V2\参考音频"

ALLOWED_TONES = {
    "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装", 
    "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气", 
    "严肃", "疑问", "自言"
}

try:
    import aiohttp
    aiohttp_available = True
except ImportError:
    print("警告: aiohttp库未安装，TTS功能可能不可用")
    aiohttp_available = False

try:
    from io import BytesIO
    BytesIO_available = True
except ImportError:
    BytesIO_available = False

def parse_text_with_tone(text: str) -> tuple:
    """
    解析带有语气标记的文本
    语气必须在允许的语气列表中，否则视为无语气标记
    
    Args:
        text: 格式为"【语气】文本内容"的字符串
    
    Returns:
        tuple: (语气, 纯文本)
    """
    if text.startswith("【") and "】" in text:
        end_idx = text.index("】")
        tone = text[1:end_idx]
        content = text[end_idx+1:].strip()
        
        if tone in ALLOWED_TONES:
            return tone, content
        else:
            print(f"警告: 语气'{tone}'不在允许列表中，将作为普通文本处理")
            return "", text
    
    return "", text

def is_valid_tone(tone: str) -> bool:
    """
    检查语气是否在允许的列表中
    
    Args:
        tone: 要检查的语气
    
    Returns:
        bool: 是否为有效语气
    """
    return tone in ALLOWED_TONES

def get_allowed_tones() -> list:
    """
    获取所有允许的语气列表
    
    Returns:
        list: 允许的语气列表
    """
    return sorted(list(ALLOWED_TONES))

def find_ref_audio_by_tone(tone: str, ref_audio_dir: str = REF_AUDIO_PATH) -> str:
    """
    根据语气查找参考音频文件
    
    Args:
        tone: 语气标识
        ref_audio_dir: 参考音频目录
    
    Returns:
        str: 找到的参考音频路径，未找到返回空字符串
    """
    if not tone:
        return ""
    
    if not os.path.exists(ref_audio_dir):
        print(f"警告: 参考音频目录不存在: {ref_audio_dir}")
        return ""
    
    for filename in os.listdir(ref_audio_dir):
        if tone in filename:
            return os.path.join(ref_audio_dir, filename)
    
    print(f"警告: 未找到带有语气'{tone}'的参考音频文件")
    return ""

def parse_args():
    """
    解析命令行参数
    与主程序风格保持一致
    """
    parser = argparse.ArgumentParser(description="音频处理主程序 - 提供音频信息获取和播放功能")
    parser.add_argument("audio_path", nargs="?", help="音频文件路径")
    parser.add_argument("--play", action="store_true", help="播放音频")
    parser.add_argument("--duration", type=float, help="播放时长（秒）")
    parser.add_argument("--info-only", action="store_true", help="仅显示音频信息，不播放")
    parser.add_argument("--as-microphone", action="store_true", help="将音频文件作为麦克风输入")
    parser.add_argument("--output-device", type=int, help="指定输出设备编号")
    parser.add_argument("--list-input-devices", action="store_true", help="列出所有声音输入设备")
    parser.add_argument("--list-output-devices", action="store_true", help="列出所有声音输出设备")
    parser.add_argument("--tts", action="store_true", help="开启 TTS 功能支持")
    return parser.parse_args()

def play_audio_file(audio_path: str, duration: Optional[float] = None) -> None:
    """
    播放音频文件
    
    Args:
        audio_path: 音频文件路径
        duration: 播放时长（秒）
    """
    print("\n=== 音频播放 ===")
    
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
    
    print("正在准备播放...")
    play_result = play_audio(audio_path, duration)
    
    if play_result["success"]:
        print("\n=== 播放结果 ===")
        print(f"音频总时长: {format_duration(play_result['total_duration'])}")
        print(f"实际播放时长: {format_duration(play_result['played_duration'])}")
        print("播放完成！")
    else:
        print(f"\n播放失败: {play_result['error']}")

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
            
        if not os.path.isabs(path):
            path = os.path.abspath(path)
            
        return path

def process_audio(audio_path: str, play: bool = True, duration: Optional[float] = None, 
                  as_microphone: bool = False, output_device: Optional[int] = None) -> Dict[str, Any]:
    """
    处理音频文件（供外部调用）
    
    Args:
        audio_path: 音频文件路径
        play: 是否播放音频
        duration: 播放时长（秒）
        as_microphone: 是否作为麦克风输入
        output_device: 输出设备编号
    
    Returns:
        Dict[str, Any]: 处理结果
    """
    result = {
        "success": False,
        "audio_info": None,
        "play_result": None,
        "error": None
    }
    
    try:
        verify_result = verify_audio_file(audio_path)
        if not verify_result["exists"]:
            result["error"] = verify_result["error"]
            return result
        
        audio_info = get_audio_info(audio_path)
        result["audio_info"] = audio_info
        
        if not audio_info["success"]:
            result["error"] = audio_info["error"]
            return result
        
        if not play:
            result["success"] = True
            return result
        
        if as_microphone:
            mic_result = audio_as_microphone(audio_path, output_device)
            result["play_result"] = mic_result
            result["success"] = mic_result["success"]
            if not mic_result["success"]:
                result["error"] = mic_result["error"]
        else:
            play_result = play_audio(audio_path, duration)
            result["play_result"] = play_result
            result["success"] = play_result["success"]
            if not play_result["success"]:
                result["error"] = play_result["error"]
        
        return result
        
    except Exception as e:
        result["error"] = str(e)
        return result

async def tts_request(text: str, prompt_text: str = "", 
                      ref_audio_path: str = "", 
                      host: str = TTS_HOST, port: int = TTS_PORT) -> Dict[str, Any]:
    """
    发送TTS请求到TTS服务端口
    使用POST请求发送JSON数据，避免中文URL编码
    使用流式读取确保完整接收音频数据
    
    Args:
        text: 要合成的文本
        prompt_text: 提示文本（语气/风格参考）
        ref_audio_path: 参考音频路径
        host: TTS服务地址
        port: TTS服务端口
    
    Returns:
        Dict[str, Any]: TTS请求结果
            - success: bool, 请求是否成功
            - audio_data: bytes, 音频数据（如果成功）
            - error: str, 错误信息（如果失败）
            - content_length: int, 响应内容长度
    """
    result = {
        "success": False,
        "audio_data": None,
        "error": None,
        "content_length": 0
    }
    
    if not aiohttp_available:
        result["error"] = "aiohttp库未安装，无法发送TTS请求"
        return result
    
    if not text:
        result["error"] = "text参数不能为空"
        return result
    
    url = f"http://{host}:{port}/tts"
    
    data = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": ref_audio_path if ref_audio_path else "archive_jingyuan_1.wav",
        "prompt_text": prompt_text,
        "prompt_lang": "zh",
        "text_split_method": "cut1",
        "batch_size": 4,
        "batch_threshold": 0.5,
        "media_type": "wav",
        "streaming_mode": 3
    }
    
    print(f"TTS请求参数: {json.dumps(data, ensure_ascii=False)}")
    
    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data) as response:
                print(f"TTS请求响应状态码: {response.status}")
                
                content_length = response.headers.get('Content-Length')
                content_type = response.headers.get('Content-Type')
                print(f"响应Content-Type: {content_type}")
                
                if content_length:
                    result["content_length"] = int(content_length)
                    print(f"预期内容长度: {content_length} bytes")
                
                if response.status == 200:
                    raw_audio_data = bytearray()
                    chunk_size = 8192
                    total_received = 0
                    chunk_count = 0
                    sample_rate = 32000
                    bits_per_sample = 16
                    is_first_chunk = True
                    
                    print("开始接收音频数据...")
                    async for chunk in response.content.iter_chunked(chunk_size):
                        if chunk:
                            total_received += len(chunk)
                            chunk_count += 1
                            
                            if len(chunk) >= 44 and chunk[:4] == b'RIFF':
                                if is_first_chunk:
                                    sample_rate = int.from_bytes(chunk[24:28], 'little')
                                    bits_per_sample = int.from_bytes(chunk[34:36], 'little')
                                    is_first_chunk = False
                                
                                fmt_chunk_size = int.from_bytes(chunk[16:20], 'little')
                                data_start = 20 + fmt_chunk_size
                                
                                if data_start + 8 <= len(chunk) and chunk[data_start:data_start+4] == b'data':
                                    data_size = int.from_bytes(chunk[data_start+4:data_start+8], 'little')
                                    data_end = data_start + 8 + data_size
                                    if data_size > 0:
                                        if data_end <= len(chunk):
                                            raw_audio_data.extend(chunk[data_start+8:data_end])
                                        else:
                                            raw_audio_data.extend(chunk[data_start+8:])
                                else:
                                    raw_audio_data.extend(chunk[44:])
                            else:
                                raw_audio_data.extend(chunk)
                            
                            print(f"已接收: {total_received} bytes ({chunk_count} chunks)", end='\r')
                    
                    print()
                    
                    if len(raw_audio_data) > 0:
                        wav_data = bytearray()
                        wav_data.extend(b'RIFF')
                        wav_data.extend((len(raw_audio_data) + 36).to_bytes(4, 'little'))
                        wav_data.extend(b'WAVEfmt ')
                        wav_data.extend((16).to_bytes(4, 'little'))
                        wav_data.extend((1).to_bytes(2, 'little'))
                        wav_data.extend((1).to_bytes(2, 'little'))
                        wav_data.extend(sample_rate.to_bytes(4, 'little'))
                        wav_data.extend((sample_rate * bits_per_sample // 8).to_bytes(4, 'little'))
                        wav_data.extend((bits_per_sample // 8).to_bytes(2, 'little'))
                        wav_data.extend(bits_per_sample.to_bytes(2, 'little'))
                        wav_data.extend(b'data')
                        wav_data.extend(len(raw_audio_data).to_bytes(4, 'little'))
                        wav_data.extend(raw_audio_data)
                        
                        audio_data = bytes(wav_data)
                        print(f"✓ 流式音频组装完成，原始数据: {len(raw_audio_data)} bytes")
                    else:
                        audio_data = bytes()
                    
                    result["audio_data"] = audio_data
                    result["success"] = True
                    print(f"TTS请求成功，音频数据大小: {len(audio_data)} bytes")
                    print(f"共接收 {chunk_count} 个数据块")
                    
                    if content_length and int(content_length) != len(audio_data):
                        print(f"警告: 接收的数据大小({len(audio_data)})与预期({content_length})不一致")
                    else:
                        print("✓ 数据完整性验证通过")
                        
                    if len(audio_data) > 0:
                        print(f"音频数据前16字节: {audio_data[:16].hex()}")
                        if audio_data[:4] == b'RIFF':
                            print("✓ 检测到WAV文件格式")
                            
                            if len(audio_data) >= 44:
                                riff_size = int.from_bytes(audio_data[4:8], 'little')
                                fmt_chunk_size = int.from_bytes(audio_data[16:20], 'little')
                                sample_rate = int.from_bytes(audio_data[24:28], 'little')
                                bits_per_sample = int.from_bytes(audio_data[34:36], 'little')
                                data_chunk_size = int.from_bytes(audio_data[40:44], 'little')
                                
                                print(f"  RIFF块声明大小: {riff_size + 8} bytes")
                                print(f"  fmt块大小: {fmt_chunk_size} bytes")
                                print(f"  采样率: {sample_rate} Hz")
                                print(f"  位深度: {bits_per_sample} bits")
                                print(f"  数据块大小: {data_chunk_size} bytes")
                                print(f"  计算音频时长: {(data_chunk_size * 8) / (sample_rate * bits_per_sample):.2f} 秒")
                                
                                expected_total = riff_size + 8
                                if len(audio_data) == expected_total:
                                    print("  ✓ WAV文件结构完整")
                                else:
                                    print(f"  ⚠️ 警告: WAV文件实际大小({len(audio_data)})与RIFF块声明({expected_total})不一致")
                            else:
                                print("  ⚠️ 警告: 音频数据不足44字节，无法解析完整WAV头")
                        else:
                            print("⚠️ 警告: 数据开头不是WAV格式(RIFF)")
                            print(f"  数据开头: {audio_data[:20]}")
                else:
                    try:
                        error_info = await response.json()
                        result["error"] = f"TTS请求失败，HTTP状态码: {response.status}, 错误信息: {json.dumps(error_info, ensure_ascii=False)}"
                    except:
                        error_text = await response.text()
                        result["error"] = f"TTS请求失败，HTTP状态码: {response.status}, 响应内容: {error_text[:500]}"

    except Exception as e:
        result["error"] = f"TTS请求异常: {str(e)}"
        import traceback
        result["error"] += f"\n{traceback.format_exc()}"
    
    return result

async def tts_request_and_stream_play(text: str, prompt_text: str = "",
                                      ref_audio_path: str = "",
                                      host: str = TTS_HOST, port: int = TTS_PORT,
                                      speed_factor: float = 1.0,
                                      streaming_mode: int = 0,
                                      save_path: str = "") -> Dict[str, Any]:
    """
    发送TTS请求并边接收边播放音频流（不保存到文件）
    
    Args:
        text: 要合成的文本
        prompt_text: 提示文本（语气/风格参考）
        ref_audio_path: 参考音频路径
        host: TTS服务地址
        port: TTS服务端口
        speed_factor: 音频速度因子，小于1表示减速，大于1表示加速
        streaming_mode: 流式模式: 0=禁用, 1/2/3=启用
        save_path: 保存音频到文件路径（可选）
    
    Returns:
        Dict[str, Any]: 播放结果
            - success: bool, 操作是否成功
            - error: str, 错误信息（如果失败）
            - audio_duration: float, 音频时长（秒）
    """
    result = {
        "success": False,
        "error": None,
        "audio_duration": 0.0
    }
    
    if not aiohttp_available:
        result["error"] = "aiohttp库未安装，无法发送TTS请求"
        return result
    
    if not text:
        result["error"] = "text参数不能为空"
        return result
    
    url = f"http://{host}:{port}/tts"
    
    data = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": ref_audio_path if ref_audio_path else "archive_jingyuan_1.wav",
        "aux_ref_audio_paths": [],
        "prompt_text": prompt_text,
        "prompt_lang": "zh",
        "top_k": 5,
        "top_p": 1.0,
        "temperature": 1.0,
        "text_split_method": "cut5",
        "batch_size": 1,
        "batch_threshold": 0.75,
        "split_bucket": True,
        "speed_factor": speed_factor,
        "fragment_interval": 0.3,
        "seed": -1,
        "parallel_infer": False,
        "repetition_penalty": 1.35,
        "sample_steps": 32,
        "super_sampling": False,
        "streaming_mode": streaming_mode,
        "overlap_length": 2,
        "min_chunk_length": 16,
        "media_type": "wav"
    }
    
    print(f"TTS请求参数: {json.dumps(data, ensure_ascii=False)}")
    
    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data) as response:
                print(f"TTS请求响应状态码: {response.status}")
                
                content_type = response.headers.get('Content-Type')
                print(f"响应Content-Type: {content_type}")
                
                if response.status == 200:
                    print("开始接收音频数据...")
                    
                    is_streaming = streaming_mode != 0
                    
                    if is_streaming:
                        print("模式: 流式传输")
                        sample_rate = 32000
                        bits_per_sample = 16
                        channels = 1
                        is_first_chunk = True
                        received_wav_header = False
                        
                        raw_audio_data = bytearray()
                        chunk_count = 0
                        total_received = 0
                        
                        async for chunk in response.content.iter_chunked(8192):
                            if chunk:
                                chunk_count += 1
                                total_received += len(chunk)
                                
                                if is_first_chunk:
                                    if len(chunk) >= 44 and chunk[:4] == b'RIFF':
                                        received_wav_header = True
                                        sample_rate = int.from_bytes(chunk[24:28], 'little')
                                        bits_per_sample = int.from_bytes(chunk[34:36], 'little')
                                        channels = int.from_bytes(chunk[22:24], 'little')
                                        raw_audio_data.extend(chunk[44:])
                                        print(f"✓ 第一个块包含WAV头+音频数据，采样率: {sample_rate} Hz, 位深度: {bits_per_sample} bit, 声道数: {channels}")
                                    else:
                                        raw_audio_data.extend(chunk)
                                    is_first_chunk = False
                                else:
                                    raw_audio_data.extend(chunk)
                                
                                print(f"已接收: {total_received} bytes ({chunk_count} chunks)", end='\r')
                        
                        print()
                        
                        if len(raw_audio_data) > 0:
                            if received_wav_header:
                                audio_len = len(raw_audio_data)
                                riff_size = audio_len + 36
                                
                                full_wav = bytearray()
                                full_wav.extend(b'RIFF')
                                full_wav.extend(riff_size.to_bytes(4, 'little'))
                                full_wav.extend(b'WAVEfmt ')
                                full_wav.extend((16).to_bytes(4, 'little'))
                                full_wav.extend((1).to_bytes(2, 'little'))
                                full_wav.extend(channels.to_bytes(2, 'little'))
                                full_wav.extend(sample_rate.to_bytes(4, 'little'))
                                byte_rate = sample_rate * channels * (bits_per_sample // 8)
                                full_wav.extend(byte_rate.to_bytes(4, 'little'))
                                full_wav.extend((channels * (bits_per_sample // 8)).to_bytes(2, 'little'))
                                full_wav.extend(bits_per_sample.to_bytes(2, 'little'))
                                full_wav.extend(b'data')
                                full_wav.extend(audio_len.to_bytes(4, 'little'))
                                full_wav.extend(raw_audio_data)
                                
                                audio_to_play = bytes(full_wav)
                                print(f"✓ 流式数据组装完成，采样率: {sample_rate}, 位深度: {bits_per_sample}, 声道数: {channels}, 音频数据: {len(raw_audio_data)} bytes")
                            else:
                                audio_to_play = bytes(raw_audio_data)
                                print(f"警告: 未接收到WAV头，直接使用原始数据")
                    else:
                        print("模式: 非流式传输")
                        audio_to_play = await response.read()
                        print(f"✓ 完整音频数据接收完成，大小: {len(audio_to_play)} bytes")
                    
                    if len(audio_to_play) > 0:
                        if save_path:
                            with open(save_path, "wb") as f:
                                f.write(audio_to_play)
                            print(f"✓ 音频已保存到: {save_path}")
                        
                        play_result = play_audio_stream(audio_to_play)
                        
                        if play_result["success"]:
                            result["success"] = True
                            result["audio_duration"] = play_result["duration"]
                            print(f"✓ 流式TTS音频播放成功，时长: {format_duration(play_result['duration'])}")
                        else:
                            result["error"] = f"播放音频流失败: {play_result['error']}"
                    else:
                        result["error"] = "未接收到音频数据"
                else:
                    try:
                        error_info = await response.json()
                        result["error"] = f"TTS请求失败，HTTP状态码: {response.status}, 错误信息: {json.dumps(error_info, ensure_ascii=False)}"
                    except:
                        error_text = await response.text()
                        result["error"] = f"TTS请求失败，HTTP状态码: {response.status}, 响应内容: {error_text[:500]}"

    except Exception as e:
        result["error"] = f"TTS请求异常: {str(e)}"
        import traceback
        result["error"] += f"\n{traceback.format_exc()}"
    
    return result

async def play_tts_audio(text: str, prompt_text: str = "", 
                         ref_audio_path: str = "",
                         host: str = TTS_HOST, port: int = TTS_PORT,
                         keep_temp: bool = True) -> Dict[str, Any]:
    """
    发送TTS请求并播放生成的音频
    
    Args:
        text: 要合成的文本
        prompt_text: 提示文本（语气/风格参考）
        ref_audio_path: 参考音频路径
        host: TTS服务地址
        port: TTS服务端口
        keep_temp: 是否保留临时音频文件（默认保留）
    
    Returns:
        Dict[str, Any]: 播放结果
            - success: bool, 操作是否成功
            - error: str, 错误信息（如果失败）
            - audio_duration: float, 音频时长（秒）
            - audio_path: str, 生成的音频文件路径
    """
    result = {
        "success": False,
        "error": None,
        "audio_duration": 0.0,
        "audio_path": None
    }
    
    tts_result = await tts_request(text, prompt_text, ref_audio_path, host, port)
    
    if not tts_result["success"]:
        result["error"] = tts_result["error"]
        return result
    
    audio_data = tts_result["audio_data"]
    
    if not BytesIO_available:
        result["error"] = "BytesIO不可用，无法处理音频数据"
        return result
    
    temp_audio_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_tts.wav")
    
    try:
        with open(temp_audio_path, "wb") as f:
            f.write(audio_data)
        
        play_result = play_audio(temp_audio_path)
        
        if play_result["success"]:
            result["success"] = True
            result["audio_duration"] = play_result["total_duration"]
            result["audio_path"] = temp_audio_path
            print(f"TTS音频播放完成，时长: {format_duration(play_result['total_duration'])}")
            print(f"音频文件已保存: {temp_audio_path}")
        else:
            result["error"] = play_result["error"]
            if not keep_temp and os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
    
    except Exception as e:
        result["error"] = f"播放TTS音频异常: {str(e)}"
        if not keep_temp and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
    
    return result

def tts_node(state) -> dict:
    """
    LangGraph TTS节点
    从状态中获取文本和语气，发送TTS请求并直接播放音频流
    
    支持解析格式为"【语气】文本内容"的文本，自动根据语气查找参考音频
    
    Args:
        state: LangGraph状态对象（LLMState或dict），包含以下字段:
            - response: str, 要合成的文本（支持【语气】文本内容格式）
    
    Returns:
        dict: 更新后的状态
    """
    import logging
    
    logger = logging.getLogger("tts_node")
    logger.info("=== 执行 TTS 节点 ===")
    
    try:
        # 获取响应文本
        if isinstance(state, dict):
            text = state.get("response", "")
        else:
            text = getattr(state, "response", "")
        
        logger.info(f"TTS节点接收到的文本: {text[:50]}..." if len(text) > 50 else f"TTS节点接收到的文本: {text}")
        
        if not text:
            logger.warning("未找到要合成的文本，跳过TTS处理")
            return dict(state) if isinstance(state, dict) else state
        
        tone, content = parse_text_with_tone(text)
        prompt_text = ""
        ref_audio_path = ""
        
        if tone:
            found_audio = find_ref_audio_by_tone(tone)
            if found_audio:
                ref_audio_path = found_audio
                logger.info(f"根据语气'{tone}'找到参考音频: {os.path.basename(found_audio)}")
                
                filename = os.path.basename(found_audio)
                name_without_ext = os.path.splitext(filename)[0]
                if name_without_ext.startswith("【"):
                    end_bracket = name_without_ext.find("】")
                    if end_bracket != -1:
                        prompt_text = name_without_ext[end_bracket+1:]
            else:
                logger.warning(f"未找到语气'{tone}'对应的参考音频")
        else:
            logger.info("未指定语气，使用默认配置")
        
        print(f"\n=== TTS 语音合成 ===")
        print(f"原文: {text}")
        if tone:
            print(f"语气: '{tone}'")
        print(f"内容: '{content}'")
        if ref_audio_path:
            print(f"参考音频: {os.path.basename(ref_audio_path)}")
        if prompt_text:
            print(f"提示文本: '{prompt_text}'")
        
        # 发送 TTS 请求
        play_result = asyncio.run(tts_request_and_stream_play(content, prompt_text, ref_audio_path))
        
        # 更新状态
        if isinstance(state, dict):
            new_state = dict(state)
            if play_result["success"]:
                logger.info("TTS音频播放成功")
                print(f"✓ TTS音频播放成功，时长: {format_duration(play_result['audio_duration'])}")
                new_state["tts_played"] = True
                new_state["tts_duration"] = play_result["audio_duration"]
                new_state["tts_tone"] = tone
                new_state["tts_content"] = content
            else:
                logger.error(f"TTS音频播放失败: {play_result['error']}")
                print(f"✗ TTS音频播放失败: {play_result['error']}")
                new_state["tts_error"] = play_result["error"]
            return new_state
        else:
            if play_result["success"]:
                logger.info("TTS音频播放成功")
                print(f"✓ TTS音频播放成功，时长: {format_duration(play_result['audio_duration'])}")
                setattr(state, "tts_played", True)
                setattr(state, "tts_duration", play_result["audio_duration"])
                setattr(state, "tts_tone", tone)
                setattr(state, "tts_content", content)
            else:
                logger.error(f"TTS音频播放失败: {play_result['error']}")
                print(f"✗ TTS音频播放失败: {play_result['error']}")
                setattr(state, "tts_error", play_result["error"])
            return state
    
    except Exception as e:
        logger.error(f"TTS节点执行失败: {e}")
        import traceback
        traceback.print_exc()
        print(f"✗ TTS节点执行异常: {e}")
        
        if isinstance(state, dict):
            new_state = dict(state)
            new_state["tts_error"] = str(e)
            return new_state
        else:
            setattr(state, "tts_error", str(e))
            return state

def main():
    """
    主程序入口
    """
    global args
    args = parse_args()
    
    if args.list_input_devices:
        print("=== 声音输入设备列表 ===")
        input_devices = get_audio_input_devices()
        if input_devices["success"]:
            print(f"默认输入设备: {input_devices['default_device']}")
            print("\n可用输入设备:")
            for device_id, device_name in input_devices["devices"].items():
                is_default = "*" if device_id == input_devices["default_device"] else " "
                print(f"  {is_default} [{device_id}] {device_name}")
        else:
            print(f"获取输入设备列表失败: {input_devices['error']}")
        sys.exit(0)
    
    if args.list_output_devices:
        print("=== 声音输出设备列表 ===")
        output_devices = get_audio_output_devices()
        if output_devices["success"]:
            print(f"默认输出设备: {output_devices['default_device']}")
            print("\n可用输出设备:")
            for device_id, device_name in output_devices["devices"].items():
                is_default = "*" if device_id == output_devices["default_device"] else " "
                print(f"  {is_default} [{device_id}] {device_name}")
        else:
            print(f"获取输出设备列表失败: {output_devices['error']}")
        sys.exit(0)
    
    if args.tts:
        print("=== TTS 功能已启用 ===")
    
    if not args.audio_path:
        audio_path = get_audio_path_from_user()
        if not audio_path:
            print("未提供有效音频文件路径，程序退出")
            sys.exit(1)
    else:
        audio_path = args.audio_path
    
    print("正在验证音频文件...")
    verify_result = verify_audio_file(audio_path)
    
    if not verify_result["exists"]:
        print(f"错误: {verify_result['error']}")
        sys.exit(1)
    
    print(f"音频文件验证成功: {verify_result['file_name']} ({verify_result['format']})")
    
    print("\n正在获取音频信息...")
    audio_info = get_audio_info(audio_path)
    
    if not audio_info["success"]:
        print(f"获取音频信息失败: {audio_info['error']}")
    else:
        print_audio_info(audio_info)
    
    if not args.info_only:
        if args.as_microphone:
            print("\n=== 音频作为麦克风输入 ===")
            result = audio_as_microphone(audio_path, args.output_device)
            if result["success"]:
                print(f"音频作为麦克风输入成功，时长: {format_duration(result['duration'])}")
                print(f"使用的输出设备: {result['output_device']}")
            else:
                print(f"音频作为麦克风输入失败: {result['error']}")
        elif args.play or not args.audio_path:
            play_audio_file(audio_path, args.duration)
    elif args.info_only:
        print("\n已选择仅显示信息模式，跳过播放")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序发生错误: {e}")
        sys.exit(1)