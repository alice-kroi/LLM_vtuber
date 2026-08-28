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
import logging
from io import BytesIO
from typing import Optional, Dict, Any, Tuple

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

# 云端 TTS 模块（可选）
try:
    from cloud_tts import cloud_tts_synthesize, CloudTtsConfig, get_cloud_tts_status
    cloud_tts_available = True
except ImportError:
    cloud_tts_available = False
    cloud_tts_synthesize = None
    CloudTtsConfig = None
    get_cloud_tts_status = None

logger = logging.getLogger("audio_main")

args = None

TTS_HOST = "127.0.0.1"
TTS_PORT = 9880
REF_AUDIO_PATH = r"D:\Github\【GPT-SoVITS】爱莉希雅V2\参考音频"
TTS_MAIN_SERVER_URL = f"http://{TTS_HOST}:{TTS_PORT}"

ALLOWED_TONES = {
    "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装",
    "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气",
    "严肃", "疑问", "自言"
}

# BytesIO 始终可用（标准库）
BytesIO_available = True

try:
    import aiohttp
    aiohttp_available = True
except ImportError:
    logger.warning("aiohttp库未安装，TTS功能可能不可用")
    aiohttp_available = False


def clean_text_for_tts(text: str) -> str:
    """
    清理 TTS 文本，移除会导致 GPT-SoVITS 合成失败的字符。
    - 剔除 Unicode 表情符号 (Emoji) 及符号类扩展区
    - 剔除 Bilibili 自定义表情标签（例如 [妙] [dog]）
    - 保留中日韩字符、拉丁字母、数字、常见标点、空白

    Args:
        text: 原始文本

    Returns:
        清理后的纯文本
    """
    if not text:
        return text

    import re

    # 1. 先剔除 Bilibili 风格的方括号表情标签：[xxx]，允许 2~8 个字符
    text = re.sub(r"\[[^\[\]]{1,8}\]", "", text)

    result_chars = []
    for ch in text:
        code = ord(ch)

        # 保留 ASCII 可打印字符
        if 0x20 <= code <= 0x7E:
            result_chars.append(ch)
            continue

        # 中日韩统一表意文字（简体/繁体）
        if 0x4E00 <= code <= 0x9FFF:
            result_chars.append(ch)
            continue

        # 中文标点符号
        if 0x3000 <= code <= 0x303F:
            # 仅保留常用中文标点，跳过特殊符号
            if ch in "，。！？、：；（）《》""''「」『』—…～·":
                result_chars.append(ch)
            continue

        # 全角标点/兼容标点区块
        if 0xFF00 <= code <= 0xFFEF:
            result_chars.append(ch)
            continue

        # 平假名/片假名
        if 0x3040 <= code <= 0x30FF:
            result_chars.append(ch)
            continue

        # 常见拉丁扩展（带重音等）和希腊字母，避免误伤外来词
        if 0x0080 <= code <= 0x03FF:
            result_chars.append(ch)
            continue

        # 韩文音节
        if 0xAC00 <= code <= 0xD7AF:
            result_chars.append(ch)
            continue

        # 其他字符（Emoji 0x1Fxxx、0x26xx、0x27xx、数学符号、装饰符号等）全部丢弃

    cleaned = "".join(result_chars).strip()
    # 合并多个空白为单个空格
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def parse_text_with_tone(text: str) -> tuple:
    """
    解析带有语气标记的文本
    语气必须在允许的语气列表中，否则视为无语气标记
    支持 Live2D 格式 "【语气】文本内容|目光方向|嘴巴状态"，
    会自动去除 "|目光方向|嘴巴状态" 后缀，只保留纯文本用于 TTS 合成。

    Args:
        text: 格式为"【语气】文本内容"或"【语气】文本内容|目光|嘴巴"的字符串

    Returns:
        tuple: (语气, 纯文本)
    """
    if text.startswith("【") and "】" in text:
        end_idx = text.index("】")
        tone = text[1:end_idx]
        content = text[end_idx+1:].strip()
        # 去除 Live2D 动作后缀（格式: 内容|目光方向|嘴巴状态）
        content = content.split("|")[0].strip()

        if tone in ALLOWED_TONES:
            return tone, content
        else:
            logger.warning(f"语气'{tone}'不在允许列表中，将作为普通文本处理")
            return "", content

    # 无语气标记但也可能带 Live2D 后缀，统一去除
    if "|" in text:
        return "", text.split("|")[0].strip()
    return "", text


def is_valid_tone(tone: str) -> bool:
    """检查语气是否在允许的列表中"""
    return tone in ALLOWED_TONES


def get_allowed_tones() -> list:
    """获取所有允许的语气列表"""
    return sorted(list(ALLOWED_TONES))


def find_ref_audio_by_tone(tone: str, ref_audio_dir: str = REF_AUDIO_PATH) -> str:
    """
    根据语气查找参考音频文件。
    为避免子串误匹配（例如"开心"和"开"混淆），要求文件名中包含【tone】。
    """
    if not tone:
        return ""

    if not os.path.exists(ref_audio_dir):
        logger.warning(f"参考音频目录不存在: {ref_audio_dir}")
        return ""

    tag = f"【{tone}】"
    # 优先匹配带【语气】标签的文件
    for filename in os.listdir(ref_audio_dir):
        if tag in filename:
            return os.path.join(ref_audio_dir, filename)
    # 降级：包含语气关键字
    for filename in os.listdir(ref_audio_dir):
        if tone in filename:
            return os.path.join(ref_audio_dir, filename)

    logger.warning(f"未找到带有语气'{tone}'的参考音频文件")
    return ""


# --------- WAV 组装工具（共享给 tts_request 和 流式接收 两种模式） ---------
def _parse_wav_header_from_chunk(chunk: bytes) -> Tuple[int, int, int]:
    """
    从WAV头的第一个chunk中解析 sample_rate, bits_per_sample, channels。
    chunk 必须包含至少44字节且以 RIFF 开头。
    """
    sample_rate = int.from_bytes(chunk[24:28], 'little')
    bits_per_sample = int.from_bytes(chunk[34:36], 'little')
    channels = int.from_bytes(chunk[22:24], 'little')
    return sample_rate, bits_per_sample, channels


def _extract_pcm_from_chunk(chunk: bytes) -> bytes:
    """
    从一个 RIFF 数据块（chunk）中提取纯 PCM 数据。
    流程：跳过 RIFF+fmt 头，找到 data chunk，提取其内容。
    chunk 可能不完整地截断 data，因此只提取到当前 chunk 的末尾。
    """
    if len(chunk) < 44 or chunk[:4] != b'RIFF':
        return chunk  # 不是标准头，当纯原始数据用

    try:
        fmt_chunk_size = int.from_bytes(chunk[16:20], 'little')
        data_start = 20 + fmt_chunk_size
        if data_start + 8 <= len(chunk) and chunk[data_start:data_start + 4] == b'data':
            data_size = int.from_bytes(chunk[data_start + 4:data_start + 8], 'little')
            data_end = data_start + 8 + data_size
            if data_end <= len(chunk):
                return bytes(chunk[data_start + 8:data_end])
            # data 块超出当前chunk：把剩余全部拿走
            return bytes(chunk[data_start + 8:])
        # 找不到标准 data 块：回退：跳过 RIFF(44字节) 头部之后的所有内容
        return bytes(chunk[44:])
    except Exception:
        return bytes(chunk[44:])


def _build_wav_from_pcm(pcm: bytes, sample_rate: int, bits_per_sample: int, channels: int) -> bytes:
    """用标准 WAV 头包装 PCM 裸数据"""
    if not pcm:
        return b''
    header = bytearray()
    header.extend(b'RIFF')
    header.extend((len(pcm) + 36).to_bytes(4, 'little'))
    header.extend(b'WAVEfmt ')
    header.extend((16).to_bytes(4, 'little'))
    header.extend((1).to_bytes(2, 'little'))
    header.extend(channels.to_bytes(2, 'little'))
    header.extend(sample_rate.to_bytes(4, 'little'))
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    header.extend(byte_rate.to_bytes(4, 'little'))
    header.extend((channels * (bits_per_sample // 8)).to_bytes(2, 'little'))
    header.extend(bits_per_sample.to_bytes(2, 'little'))
    header.extend(b'data')
    header.extend(len(pcm).to_bytes(4, 'little'))
    return bytes(header) + pcm

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
                logger.info(f"TTS请求响应状态码: {response.status}")

                content_length = response.headers.get('Content-Length')
                content_type = response.headers.get('Content-Type')
                logger.info(f"响应Content-Type: {content_type}")

                if content_length:
                    result["content_length"] = int(content_length)

                if response.status == 200:
                    sample_rate = 32000
                    bits_per_sample = 16
                    channels = 1
                    raw_pcm = bytearray()
                    chunk_count = 0
                    total_received = 0

                    logger.info("开始接收音频数据...")
                    async for chunk in response.content.iter_chunked(8192):
                        if not chunk:
                            continue
                        chunk_count += 1
                        total_received += len(chunk)

                        # 首个块：从WAV头解析采样参数
                        if chunk_count == 1 and len(chunk) >= 44 and chunk[:4] == b'RIFF':
                            sample_rate, bits_per_sample, channels = _parse_wav_header_from_chunk(chunk)
                            logger.info(
                                f"解析WAV头: 采样率={sample_rate}Hz, "
                                f"位深={bits_per_sample}bit, 声道={channels}"
                            )

                        # 提取PCM数据（自动跳过RIFF头/各chunk头）
                        raw_pcm.extend(_extract_pcm_from_chunk(chunk))
                        if chunk_count % 16 == 0:
                            logger.debug(f"已接收: {total_received} bytes ({chunk_count} chunks)")

                    # 组装成标准WAV
                    audio_data = (
                        _build_wav_from_pcm(bytes(raw_pcm), sample_rate, bits_per_sample, channels)
                        if raw_pcm else b''
                    )
                    result["audio_data"] = audio_data
                    result["success"] = True
                    logger.info(
                        f"TTS请求成功: 原始PCM={len(raw_pcm)} bytes, "
                        f"WAV={len(audio_data)} bytes, chunks={chunk_count}"
                    )

                    if content_length and int(content_length) != len(audio_data):
                        logger.warning(
                            f"数据大小不匹配: 接收={len(audio_data)}, 预期={content_length}"
                        )
                else:
                    try:
                        error_info = await response.json()
                        result["error"] = (
                            f"TTS请求失败，HTTP状态码: {response.status}, "
                            f"错误信息: {json.dumps(error_info, ensure_ascii=False)}"
                        )
                    except Exception:
                        error_text = await response.text()
                        result["error"] = (
                            f"TTS请求失败，HTTP状态码: {response.status}, "
                            f"响应内容: {error_text[:500]}"
                        )

    except Exception as e:
        import traceback
        result["error"] = f"TTS请求异常: {str(e)}\n{traceback.format_exc()}"
        logger.error(result["error"])

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
    result: Dict[str, Any] = {
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

    logger.info(f"TTS请求参数: {json.dumps(data, ensure_ascii=False)}")

    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data) as response:
                logger.info(f"TTS流式响应状态码: {response.status}")

                if response.status != 200:
                    try:
                        error_info = await response.json()
                        result["error"] = (
                            f"TTS请求失败，HTTP状态码: {response.status}, "
                            f"错误信息: {json.dumps(error_info, ensure_ascii=False)}"
                        )
                    except Exception:
                        error_text = await response.text()
                        result["error"] = (
                            f"TTS请求失败，HTTP状态码: {response.status}, "
                            f"响应内容: {error_text[:500]}"
                        )
                    return result

                # 非流式：直接拿到完整WAV
                if streaming_mode == 0:
                    logger.info("TTS模式: 非流式传输")
                    audio_to_play = await response.read()
                    logger.info(f"TTS完整音频: {len(audio_to_play)} bytes")
                else:
                    # 流式：拼PCM再组装WAV，复用共享工具函数
                    logger.info("TTS模式: 流式传输")
                    sample_rate, bits_per_sample, channels = 32000, 16, 1
                    raw_pcm = bytearray()
                    chunk_count = 0

                    async for chunk in response.content.iter_chunked(8192):
                        if not chunk:
                            continue
                        chunk_count += 1
                        if chunk_count == 1 and len(chunk) >= 44 and chunk[:4] == b'RIFF':
                            sample_rate, bits_per_sample, channels = _parse_wav_header_from_chunk(chunk)
                            logger.info(
                                f"TTS解析WAV头: 采样率={sample_rate}Hz, "
                                f"位深={bits_per_sample}bit, 声道={channels}"
                            )
                        raw_pcm.extend(_extract_pcm_from_chunk(chunk))

                    audio_to_play = (
                        _build_wav_from_pcm(bytes(raw_pcm), sample_rate, bits_per_sample, channels)
                        if raw_pcm else b''
                    )
                    logger.info(
                        f"TTS流式组装完成: PCM={len(raw_pcm)} bytes, "
                        f"WAV={len(audio_to_play)} bytes, chunks={chunk_count}"
                    )

                if not audio_to_play:
                    result["error"] = "未接收到音频数据"
                    return result

                if save_path:
                    with open(save_path, "wb") as f:
                        f.write(audio_to_play)
                    logger.info(f"TTS音频已保存到: {save_path}")

                play_result = play_audio_stream(audio_to_play)
                if play_result["success"]:
                    result["success"] = True
                    result["audio_duration"] = play_result["duration"]
                    logger.info(f"TTS播放成功，时长: {format_duration(play_result['duration'])}")
                else:
                    result["error"] = f"播放音频流失败: {play_result['error']}"
                    logger.error(result["error"])

    except Exception as e:
        import traceback
        result["error"] = f"TTS请求异常: {str(e)}\n{traceback.format_exc()}"
        logger.error(result["error"])

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

async def tts_node(state) -> dict:
    """
    LangGraph TTS节点（异步）
    从状态中获取文本和语气，发送TTS请求并直接播放音频流。
    注意：tts_node 由 LangGraph 通过 ainvoke 在已有事件循环中调用，
         因此必须为 async def，内部直接 await 异步函数，不能用 asyncio.run()。

    支持解析格式为"【语气】文本内容"的文本，自动根据语气查找参考音频

    Args:
        state: LangGraph状态对象（LLMState或dict），包含以下字段:
            - response: str, 要合成的文本（支持【语气】文本内容格式）

    Returns:
        dict: 更新后的状态
    """
    logger.info("=== 执行 TTS 节点 ===")

    try:
        # 获取响应文本
        if isinstance(state, dict):
            text = state.get("response", "")
        else:
            text = getattr(state, "response", "")

        if len(text) > 50:
            logger.info(f"TTS节点接收到的文本: {text[:50]}...")
        else:
            logger.info(f"TTS节点接收到的文本: {text}")

        if not text:
            logger.warning("未找到要合成的文本，跳过TTS处理")
            return dict(state) if isinstance(state, dict) else state

        tone, content = parse_text_with_tone(text)

        # 剔除 Emoji 和 Bilibili 表情标签，防止 GPT-SoVITS 合成报错
        # （例如 "嘿嘿～是不是坐着的样子看起来小小的超可爱呀😘" → "嘿嘿～是不是坐着的样子看起来小小的超可爱呀"）
        cleaned_content = clean_text_for_tts(content)
        if cleaned_content != content:
            removed_len = len(content) - len(cleaned_content)
            logger.info(f"TTS文本清洗: 移除了 {removed_len} 个字符 (表情/符号)")
            content = cleaned_content

        if not content:
            logger.warning("清洗后文本为空，跳过TTS处理")
            return dict(state) if isinstance(state, dict) else state

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
                        prompt_text = name_without_ext[end_bracket + 1:]
            else:
                logger.warning(f"未找到语气'{tone}'对应的参考音频")
        else:
            logger.info("未指定语气，使用默认配置")

        logger.info(
            f"TTS参数: tone={tone or '普通'}, content_len={len(content)}, "
            f"ref={os.path.basename(ref_audio_path) if ref_audio_path else '默认'}"
        )

        # 直接 await，避免 asyncio.run() 导致的事件循环冲突
        play_result = await tts_request_and_stream_play(content, prompt_text, ref_audio_path)

        # 更新状态
        def _apply(s: dict) -> dict:
            ns = dict(s)
            if play_result["success"]:
                logger.info(
                    f"TTS音频播放成功，时长: {format_duration(play_result['audio_duration'])}"
                )
                ns["tts_played"] = True
                ns["tts_duration"] = play_result["audio_duration"]
                ns["tts_tone"] = tone
                ns["tts_content"] = content
            else:
                logger.error(f"TTS音频播放失败: {play_result['error']}")
                ns["tts_error"] = play_result["error"]
            return ns

        if isinstance(state, dict):
            return _apply(state)
        else:
            # TypedDict / dataclass-like：先转成dict再写回
            state_dict = {k: state[k] for k in state} if hasattr(state, "__getitem__") else dict(state)
            updated = _apply(state_dict)
            try:
                for k, v in updated.items():
                    setattr(state, k, v)
            except Exception:
                pass
            return state

    except Exception as e:
        import traceback
        logger.error(f"TTS节点执行失败: {e}\n{traceback.format_exc()}")
        if isinstance(state, dict):
            new_state = dict(state)
            new_state["tts_error"] = str(e)
            return new_state
        else:
            try:
                setattr(state, "tts_error", str(e))
            except Exception:
                pass
            return state


# ---------- 云端 TTS 节点 ----------

_cloud_tts_config: Optional[CloudTtsConfig] = None


def set_cloud_tts_config(config: CloudTtsConfig):
    """设置云端 TTS 配置"""
    global _cloud_tts_config
    _cloud_tts_config = config
    if cloud_tts_available:
        status = get_cloud_tts_status(config)
        logger.info(f"云端TTS配置已更新: {status}")
    else:
        logger.warning("云端TTS模块不可用，请安装依赖")


def get_cloud_tts_config() -> Optional[CloudTtsConfig]:
    """获取当前云端 TTS 配置"""
    return _cloud_tts_config


async def cloud_tts_request_and_play(
    text: str,
    config: Optional[CloudTtsConfig] = None,
    streaming: bool = False,
) -> Dict[str, Any]:
    """
    云端 TTS 合成并播放
    
    Args:
        text: 要合成的文本
        config: TTS配置，为None时使用全局配置
        streaming: 是否使用流式模式
    
    Returns:
        Dict[str, Any]: 播放结果
    """
    result: Dict[str, Any] = {
        "success": False,
        "error": None,
        "audio_duration": 0.0,
    }

    cfg = config or _cloud_tts_config
    if cfg is None or not cfg.enabled:
        result["error"] = "云端TTS未启用"
        return result

    if not cloud_tts_available:
        result["error"] = "云端TTS模块不可用"
        return result

    if not text:
        result["error"] = "文本为空"
        return result

    synthesize_result = await cloud_tts_synthesize(text, cfg, streaming=streaming)

    if not synthesize_result.get("success"):
        result["error"] = synthesize_result.get("error", "合成失败")
        logger.error(f"云端TTS合成失败: {result['error']}")
        return result

    audio_data = synthesize_result.get("audio_data", b"")
    if not audio_data:
        result["error"] = "未获取到音频数据"
        return result

    audio_sample_rate = synthesize_result.get("sample_rate", 24000)
    audio_format = synthesize_result.get("format", "pcm")

    logger.info(f"云端TTS音频: {len(audio_data)} bytes, 采样率={audio_sample_rate}Hz, 格式={audio_format}, 开始播放...")

    play_result = play_audio_stream(audio_data, sample_rate=audio_sample_rate)
    if play_result["success"]:
        result["success"] = True
        result["audio_duration"] = play_result["duration"]
        logger.info(f"云端TTS播放成功，时长: {format_duration(play_result['duration'])}")
    else:
        result["error"] = f"播放失败: {play_result.get('error', 'unknown')}"
        logger.error(result["error"])

    return result


async def cloud_tts_node(state) -> dict:
    """
    LangGraph 云端 TTS 节点（异步）
    
    与 tts_node 功能相同，但使用云端 API 替代本地 GPT-SoVITS
    
    Args:
        state: LangGraph状态对象
    
    Returns:
        dict: 更新后的状态
    """
    logger.info("=== 执行云端 TTS 节点 ===")

    try:
        # 检查云端 TTS 是否仍然启用（支持运行时禁用）
        cfg = get_cloud_tts_config()
        if not cfg or not cfg.enabled:
            logger.info("云端 TTS 已禁用，跳过合成")
            return dict(state) if isinstance(state, dict) else state

        if isinstance(state, dict):
            text = state.get("response", "")
        else:
            text = getattr(state, "response", "")

        if len(text) > 50:
            logger.info(f"云端TTS节点接收文本: {text[:50]}...")
        else:
            logger.info(f"云端TTS节点接收文本: {text}")

        if not text:
            logger.warning("未找到要合成的文本，跳过云端TTS")
            return dict(state) if isinstance(state, dict) else state

        tone, content = parse_text_with_tone(text)

        cleaned_content = clean_text_for_tts(content)
        if cleaned_content != content:
            removed_len = len(content) - len(cleaned_content)
            logger.info(f"云端TTS文本清洗: 移除了 {removed_len} 个字符")
            content = cleaned_content

        if not content:
            logger.warning("清洗后文本为空，跳过云端TTS")
            return dict(state) if isinstance(state, dict) else state

        play_result = await cloud_tts_request_and_play(content)

        def _apply(s: dict) -> dict:
            ns = dict(s)
            if play_result["success"]:
                ns["cloud_tts_played"] = True
                ns["cloud_tts_duration"] = play_result.get("audio_duration", 0.0)
                logger.info(f"云端TTS播放完成，时长: {play_result.get('audio_duration', 0.0):.2f}s")
            else:
                ns["cloud_tts_played"] = False
                ns["cloud_tts_error"] = play_result.get("error", "unknown")
                logger.warning(f"云端TTS播放失败: {play_result.get('error')}")
            return ns

        if isinstance(state, dict):
            return _apply(state)
        else:
            state_dict = {k: state[k] for k in state} if hasattr(state, "__getitem__") else dict(state)
            updated = _apply(state_dict)
            try:
                for k, v in updated.items():
                    setattr(state, k, v)
            except Exception:
                pass
            return state

    except Exception as e:
        import traceback
        logger.error(f"云端TTS节点执行失败: {e}\n{traceback.format_exc()}")
        if isinstance(state, dict):
            new_state = dict(state)
            new_state["cloud_tts_error"] = str(e)
            return new_state
        else:
            try:
                setattr(state, "cloud_tts_error", str(e))
            except Exception:
                pass
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