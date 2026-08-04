"""
# WebAPI文档

` python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml `

## 执行参数:
    `-a` - `绑定地址, 默认"127.0.0.1"`
    `-p` - `绑定端口, 默认9880`
    `-c` - `TTS配置文件路径, 默认"GPT_SoVITS/configs/tts_infer.yaml"`

## 调用:

### 推理

端点: `/tts`
GET请求:
```
http://127.0.0.1:9880/tts?text=先帝创业未半而中道崩殂，今天下三分，益州疲弊，此诚危急存亡之秋也。&text_lang=zh&ref_audio_path=archive_jingyuan_1.wav&prompt_lang=zh&prompt_text=我是「罗浮」云骑将军景元。不必拘谨，「将军」只是一时的身份，你称呼我景元便可&text_split_method=cut5&batch_size=1&media_type=wav&streaming_mode=true
```

POST请求:
```json
{
    "text": "",                   # 字符串(必填) 要合成的文本
    "text_lang: "",               # 字符串(必填) 要合成文本的语言
    "ref_audio_path": "",         # 字符串(必填) 参考音频路径
    "aux_ref_audio_paths": [],    # 列表(可选) 用于多说话人语气融合的辅助参考音频路径
    "prompt_text": "",            # 字符串(可选) 参考音频的提示文本
    "prompt_lang": "",            # 字符串(必填) 参考音频提示文本的语言
    "top_k": 5,                   # 整数 Top-K 采样
    "top_p": 1,                   # 浮点数 Top-P 采样
    "temperature": 1,             # 浮点数 采样温度
    "text_split_method": "cut5",  # 字符串 文本分割方法，详见 text_segmentation_method.py
    "batch_size": 1,              # 整数 推理批处理大小
    "batch_threshold": 0.75,      # 浮点数 批处理分割阈值
    "split_bucket": True,         # 布尔值 是否将批次分割到多个桶中
    "speed_factor": 1.0,          # 浮点数 控制合成音频的速度
    "fragment_interval": 0.3,     # 浮点数 控制音频片段的间隔
    "seed": -1,                   # 整数 随机种子，用于可重复性
    "parallel_infer": True,       # 布尔值 是否使用并行推理
    "repetition_penalty": 1.35,   # 浮点数 T2S模型的重复惩罚
    "sample_steps": 32,           # 整数 VITS模型V3的采样步数
    "super_sampling": False,      # 布尔值 使用VITS模型V3时是否使用超采样
    "streaming_mode": False,      # 布尔值或整数 逐块返回音频。可用选项: 0,1,2,3 或 True/False (0/False: 禁用 | 1/True: 最佳质量，响应速度最慢(旧版streaming_mode) | 2: 中等质量，响应速度较慢 | 3: 较低质量，响应速度较快)
    "overlap_length": 2,          # 整数 流式模式下语义token的重叠长度
    "min_chunk_length": 16,       # 整数 流式模式下语义token的最小块长度(影响音频块大小)
}
```

响应:
成功: 直接返回 wav 音频流, HTTP状态码 200
失败: 返回包含错误信息的 JSON, HTTP状态码 400

### 命令控制

端点: `/control`

命令:
"restart": 重新运行
"exit": 结束运行

GET请求:
```
http://127.0.0.1:9880/control?command=restart
```
POST请求:
```json
{
    "command": "restart"
}
```

响应: 无


### 切换GPT模型

端点: `/set_gpt_weights`

GET请求:
```
http://127.0.0.1:9880/set_gpt_weights?weights_path=GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt
```
响应:
成功: 返回"success", HTTP状态码 200
失败: 返回包含错误信息的 JSON, HTTP状态码 400


### 切换Sovits模型

端点: `/set_sovits_weights`

GET请求:
```
http://127.0.0.1:9880/set_sovits_weights?weights_path=GPT_SoVITS/pretrained_models/s2G488k.pth
```

响应:
成功: 返回"success", HTTP状态码 200
失败: 返回包含错误信息的 JSON, HTTP状态码 400

"""

import os
import sys
import traceback
from typing import Generator, Union

now_dir = os.getcwd()
sys.path.append(now_dir)
sys.path.append("%s/GPT_SoVITS" % (now_dir))

import argparse
import subprocess
import wave
import signal
import numpy as np
import soundfile as sf
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from io import BytesIO
from tools.i18n.i18n import I18nAuto
from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
from GPT_SoVITS.TTS_infer_pack.text_segmentation_method import get_method_names as get_cut_method_names
from pydantic import BaseModel
import threading

# print(sys.path)
i18n = I18nAuto()
cut_method_names = get_cut_method_names()

parser = argparse.ArgumentParser(description="GPT-SoVITS api")
parser.add_argument("-c", "--tts_config", type=str, default="GPT_SoVITS/configs/tts_infer.yaml", help="tts_infer路径")
parser.add_argument("-a", "--bind_addr", type=str, default="127.0.0.1", help="default: 127.0.0.1")
parser.add_argument("-p", "--port", type=int, default="9880", help="default: 9880")
args = parser.parse_args()
config_path = args.tts_config
# device = args.device
port = args.port
host = args.bind_addr
argv = sys.argv

if config_path in [None, ""]:
    config_path = "GPT-SoVITS/configs/tts_infer.yaml"

tts_config = TTS_Config(config_path)
print(tts_config)
tts_pipeline = TTS(tts_config)

APP = FastAPI()


class TTS_Request(BaseModel):
    text: str = None
    text_lang: str = None
    ref_audio_path: str = None
    aux_ref_audio_paths: list = None
    prompt_lang: str = None
    prompt_text: str = ""
    top_k: int = 5
    top_p: float = 1
    temperature: float = 1
    text_split_method: str = "cut5"
    batch_size: int = 1
    batch_threshold: float = 0.75
    split_bucket: bool = True
    speed_factor: float = 1.0
    fragment_interval: float = 0.3
    seed: int = -1
    media_type: str = "wav"
    streaming_mode: Union[bool, int] = False
    parallel_infer: bool = True
    repetition_penalty: float = 1.35
    sample_steps: int = 32
    super_sampling: bool = False
    overlap_length: int = 2
    min_chunk_length: int = 16


def pack_ogg(io_buffer: BytesIO, data: np.ndarray, rate: int):
    # 作者: AkagawaTsurunaki
    # 问题:
    #   当使用Python库`soundfile`调用`libsndfile_64bit.dll`的`sf_writef_short`函数时，
    #   可能会发生堆栈溢出
    # 注意:
    #   这是与`libsndfile`相关的问题，不是本项目本身的问题。
    #   当生成大型音频张量(在我的PC上约499804帧)并尝试转换为ogg文件时会发生。
    # 相关链接:
    #   https://github.com/RVC-Boss/GPT-SoVITS/issues/1199
    #   https://github.com/libsndfile/libsndfile/issues/1023
    #   https://github.com/bastibe/python-soundfile/issues/396
    # 建议:
    #   或者将整个音频数据分割成较小的音频段以避免堆栈溢出?

    def handle_pack_ogg():
        with sf.SoundFile(io_buffer, mode="w", samplerate=rate, channels=1, format="ogg") as audio_file:
            audio_file.write(data)



    # 参考: https://docs.python.org/3/library/threading.html
    # 此线程的堆栈大小至少为32768
    # 如果仍然发生堆栈溢出错误，只需修改`stack_size`。
    # stack_size = n * 4096，其中n应为正整数。
    # 这里我们选择n = 4096。
    stack_size = 4096 * 4096
    try:
        threading.stack_size(stack_size)
        pack_ogg_thread = threading.Thread(target=handle_pack_ogg)
        pack_ogg_thread.start()
        pack_ogg_thread.join()
    except RuntimeError as e:
        # 如果不支持更改线程堆栈大小，会引发RuntimeError。
        print("RuntimeError: {}".format(e))
        print("不支持更改线程堆栈大小。")
    except ValueError as e:
        # 如果指定的堆栈大小无效，会引发ValueError，并且堆栈大小保持不变。
        print("ValueError: {}".format(e))
        print("指定的堆栈大小无效。")

    return io_buffer


def pack_raw(io_buffer: BytesIO, data: np.ndarray, rate: int):
    io_buffer.write(data.tobytes())
    return io_buffer


def pack_wav(io_buffer: BytesIO, data: np.ndarray, rate: int):
    io_buffer = BytesIO()
    sf.write(io_buffer, data, rate, format="wav")
    return io_buffer


def pack_aac(io_buffer: BytesIO, data: np.ndarray, rate: int):
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-f",
            "s16le",  # 输入16位有符号小端整数PCM
            "-ar",
            str(rate),  # 设置采样率
            "-ac",
            "1",  # 单声道
            "-i",
            "pipe:0",  # 从管道读取输入
            "-c:a",
            "aac",  # 音频编码器为AAC
            "-b:a",
            "192k",  # 比特率
            "-vn",  # 不包含视频
            "-f",
            "adts",  # 输出AAC数据流格式
            "pipe:1",  # 将输出写入管道
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, _ = process.communicate(input=data.tobytes())
    io_buffer.write(out)
    return io_buffer


def pack_audio(io_buffer: BytesIO, data: np.ndarray, rate: int, media_type: str):
    if media_type == "ogg":
        io_buffer = pack_ogg(io_buffer, data, rate)
    elif media_type == "aac":
        io_buffer = pack_aac(io_buffer, data, rate)
    elif media_type == "wav":
        io_buffer = pack_wav(io_buffer, data, rate)
    else:
        io_buffer = pack_raw(io_buffer, data, rate)
    io_buffer.seek(0)
    return io_buffer


# 来源: https://huggingface.co/spaces/coqui/voice-chat-with-mistral/blob/main/app.py
def wave_header_chunk(frame_input=b"", channels=1, sample_width=2, sample_rate=32000):
    # 这将创建一个wave头，然后附加帧输入
    # 它应该在流式wav文件的开头
    # 其他帧最好不要有它(否则你会在每个块开始时听到一些伪影)
    wav_buf = BytesIO()
    with wave.open(wav_buf, "wb") as vfout:
        vfout.setnchannels(channels)
        vfout.setsampwidth(sample_width)
        vfout.setframerate(sample_rate)
        vfout.writeframes(frame_input)

    wav_buf.seek(0)
    return wav_buf.read()


def handle_control(command: str):
    if command == "restart":
        os.execl(sys.executable, sys.executable, *argv)
    elif command == "exit":
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)


def check_params(req: dict):
    text: str = req.get("text", "")
    text_lang: str = req.get("text_lang", "")
    ref_audio_path: str = req.get("ref_audio_path", "")
    streaming_mode: bool = req.get("streaming_mode", False)
    media_type: str = req.get("media_type", "wav")
    prompt_lang: str = req.get("prompt_lang", "")
    text_split_method: str = req.get("text_split_method", "cut5")

    if ref_audio_path in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "ref_audio_path is required"})
    if text in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text is required"})
    if text_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "text_lang is required"})
    elif text_lang.lower() not in tts_config.languages:
        return JSONResponse(
            status_code=400,
            content={"message": f"text_lang: {text_lang} is not supported in version {tts_config.version}"},
        )
    if prompt_lang in [None, ""]:
        return JSONResponse(status_code=400, content={"message": "prompt_lang is required"})
    elif prompt_lang.lower() not in tts_config.languages:
        return JSONResponse(
            status_code=400,
            content={"message": f"prompt_lang: {prompt_lang} is not supported in version {tts_config.version}"},
        )
    if media_type not in ["wav", "raw", "ogg", "aac"]:
        return JSONResponse(status_code=400, content={"message": f"media_type: {media_type} is not supported"})
    # elif media_type == "ogg" and not streaming_mode:
    #     return JSONResponse(status_code=400, content={"message": "ogg format is not supported in non-streaming mode"})

    if text_split_method not in cut_method_names:
        return JSONResponse(
            status_code=400, content={"message": f"text_split_method:{text_split_method} is not supported"}
        )

    return None


async def tts_handle(req: dict):
    """
    文本转语音处理器。

    参数:
        req (dict):
            {
                "text": "",                   # 字符串(必填) 要合成的文本
                "text_lang: "",               # 字符串(必填) 要合成文本的语言
                "ref_audio_path": "",         # 字符串(必填) 参考音频路径
                "aux_ref_audio_paths": [],    # 列表(可选) 用于多说话人语气融合的辅助参考音频路径
                "prompt_text": "",            # 字符串(可选) 参考音频的提示文本
                "prompt_lang": "",            # 字符串(必填) 参考音频提示文本的语言
                "top_k": 5,                   # 整数 Top-K 采样
                "top_p": 1,                   # 浮点数 Top-P 采样
                "temperature": 1,             # 浮点数 采样温度
                "text_split_method": "cut5",  # 字符串 文本分割方法，详见 text_segmentation_method.py
                "batch_size": 1,              # 整数 推理批处理大小
                "batch_threshold": 0.75,      # 浮点数 批处理分割阈值
                "split_bucket": True,         # 布尔值 是否将批次分割到多个桶中
                "speed_factor": 1.0,          # 浮点数 控制合成音频的速度
                "fragment_interval": 0.3,     # 浮点数 控制音频片段的间隔
                "seed": -1,                   # 整数 随机种子，用于可重复性
                "parallel_infer": True,       # 布尔值 是否使用并行推理
                "repetition_penalty": 1.35,   # 浮点数 T2S模型的重复惩罚
                "sample_steps": 32,           # 整数 VITS模型V3的采样步数
                "super_sampling": False,      # 布尔值 使用VITS模型V3时是否使用超采样
                "streaming_mode": False,      # 布尔值或整数 逐块返回音频。可用选项: 0,1,2,3 或 True/False (0/False: 禁用 | 1/True: 最佳质量，响应速度最慢(旧版streaming_mode) | 2: 中等质量，响应速度较慢 | 3: 较低质量，响应速度较快)
                "overlap_length": 2,          # 整数 流式模式下语义token的重叠长度
                "min_chunk_length": 16,       # 整数 流式模式下语义token的最小块长度(影响音频块大小)
            }
    返回:
        StreamingResponse: 音频流响应。
    """

    streaming_mode = req.get("streaming_mode", False)
    return_fragment = req.get("return_fragment", False)
    media_type = req.get("media_type", "wav")

    check_res = check_params(req)
    if check_res is not None:
        return check_res
    
    if streaming_mode == 0:
        streaming_mode = False
        return_fragment = False
        fixed_length_chunk = False
    elif streaming_mode == 1:
        streaming_mode = False
        return_fragment = True
        fixed_length_chunk = False
    elif streaming_mode == 2:
        streaming_mode = True
        return_fragment = False
        fixed_length_chunk = False
    elif streaming_mode == 3:
        streaming_mode = True
        return_fragment = False
        fixed_length_chunk = True

    else:
        return JSONResponse(status_code=400, content={"message": f"the value of streaming_mode must be 0, 1, 2, 3(int) or true/false(bool)"})

    req["streaming_mode"] = streaming_mode
    req["return_fragment"] = return_fragment
    req["fixed_length_chunk"] = fixed_length_chunk

    print(f"{streaming_mode} {return_fragment} {fixed_length_chunk}")

    streaming_mode = streaming_mode or return_fragment


    try:
        tts_generator = tts_pipeline.run(req)

        if streaming_mode:

            def streaming_generator(tts_generator: Generator, media_type: str):
                if_frist_chunk = True
                for sr, chunk in tts_generator:
                    if if_frist_chunk and media_type == "wav":
                        yield wave_header_chunk(sample_rate=sr)
                        media_type = "raw"
                        if_frist_chunk = False
                    yield pack_audio(BytesIO(), chunk, sr, media_type).getvalue()

            # _media_type = f"audio/{media_type}" if not (streaming_mode and media_type in ["wav", "raw"]) else f"audio/x-{media_type}"
            return StreamingResponse(
                streaming_generator(
                    tts_generator,
                    media_type,
                ),
                media_type=f"audio/{media_type}",
            )

        else:
            sr, audio_data = next(tts_generator)
            audio_data = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
            return Response(audio_data, media_type=f"audio/{media_type}")
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "tts failed", "Exception": str(e)})


@APP.get("/control")
async def control(command: str = None):
    if command is None:
        return JSONResponse(status_code=400, content={"message": "command is required"})
    handle_control(command)


@APP.get("/tts")
async def tts_get_endpoint(
    text: str = None,
    text_lang: str = None,
    ref_audio_path: str = None,
    aux_ref_audio_paths: list = None,
    prompt_lang: str = None,
    prompt_text: str = "",
    top_k: int = 5,
    top_p: float = 1,
    temperature: float = 1,
    text_split_method: str = "cut5",
    batch_size: int = 1,
    batch_threshold: float = 0.75,
    split_bucket: bool = True,
    speed_factor: float = 1.0,
    fragment_interval: float = 0.3,
    seed: int = -1,
    media_type: str = "wav",
    parallel_infer: bool = True,
    repetition_penalty: float = 1.35,
    sample_steps: int = 32,
    super_sampling: bool = False,
    streaming_mode: Union[bool, int] = False,
    overlap_length: int = 2,
    min_chunk_length: int = 16,
):
    req = {
        "text": text,
        "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "aux_ref_audio_paths": aux_ref_audio_paths,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang.lower(),
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "text_split_method": text_split_method,
        "batch_size": int(batch_size),
        "batch_threshold": float(batch_threshold),
        "speed_factor": float(speed_factor),
        "split_bucket": split_bucket,
        "fragment_interval": fragment_interval,
        "seed": seed,
        "media_type": media_type,
        "streaming_mode": streaming_mode,
        "parallel_infer": parallel_infer,
        "repetition_penalty": float(repetition_penalty),
        "sample_steps": int(sample_steps),
        "super_sampling": super_sampling,
        "overlap_length": int(overlap_length),
        "min_chunk_length": int(min_chunk_length),
    }
    return await tts_handle(req)


@APP.post("/tts")
async def tts_post_endpoint(request: TTS_Request):
    req = request.dict()
    return await tts_handle(req)


@APP.get("/set_refer_audio")
async def set_refer_aduio(refer_audio_path: str = None):
    try:
        tts_pipeline.set_ref_audio(refer_audio_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "set refer audio failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})


# @APP.post("/set_refer_audio")
# async def set_refer_aduio_post(audio_file: UploadFile = File(...)):
#     try:
#         # 检查文件类型，确保是音频文件
#         if not audio_file.content_type.startswith("audio/"):
#             return JSONResponse(status_code=400, content={"message": "file type is not supported"})

#         os.makedirs("uploaded_audio", exist_ok=True)
#         save_path = os.path.join("uploaded_audio", audio_file.filename)
#         # 保存音频文件到服务器上的一个目录
#         with open(save_path , "wb") as buffer:
#             buffer.write(await audio_file.read())

#         tts_pipeline.set_ref_audio(save_path)
#     except Exception as e:
#         return JSONResponse(status_code=400, content={"message": f"set refer audio failed", "Exception": str(e)})
#     return JSONResponse(status_code=200, content={"message": "success"})


@APP.get("/set_gpt_weights")
async def set_gpt_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "gpt weight path is required"})
        tts_pipeline.init_t2s_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "change gpt weight failed", "Exception": str(e)})

    return JSONResponse(status_code=200, content={"message": "success"})


@APP.get("/set_sovits_weights")
async def set_sovits_weights(weights_path: str = None):
    try:
        if weights_path in ["", None]:
            return JSONResponse(status_code=400, content={"message": "sovits weight path is required"})
        tts_pipeline.init_vits_weights(weights_path)
    except Exception as e:
        return JSONResponse(status_code=400, content={"message": "change sovits weight failed", "Exception": str(e)})
    return JSONResponse(status_code=200, content={"message": "success"})


if __name__ == "__main__":
    try:
        if host == "None":  # 在调用时使用 -a None 参数，可以让api监听双栈
            host = None
        uvicorn.run(app=APP, host=host, port=port, workers=1)
    except Exception:
        traceback.print_exc()
        os.kill(os.getpid(), signal.SIGTERM)
        exit(0)
