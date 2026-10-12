#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三模态同步采集器：VTS 参数 + 录音 + 文字，共用一条单调时间轴。

目标：同一时间点拿到 (Live2D 参数, 声音, 文字) 三份数据，供情感分析。

时间同步方案（核心，务必理解）：
- 单一采集进程内，用 time.perf_counter() 作为共享单调时钟
  （不受系统时间调整影响，跨三流唯一可靠参照）。
- VTS 参数：每帧记录 t_mono（get_tracking_parameters 返回即打点）。
- 录音：sounddevice InputStream 回调按采样帧计数；
  音频任意时刻的绝对时间 = t0_audio_mono + frame_index / sample_rate（帧级精度）。
- 文字：事后 ASR 把 wav 转成分段时间文本（相对音频起点秒），
  换算回 mono：t_mono = t0_audio_mono + t_audio，即与 VTS 参数帧对齐。

产物（同前缀）：
- <prefix>_params.csv  : t_mono 时间轴上的 VTS 参数流（含 face_found）
- <prefix>.wav        : 同步录音（16k mono int16，ASR 友好）
- <prefix>_meta.json  : 对齐元数据（t0_audio_mono、采样率、时长等），对齐工具依赖它

用法：
    python face_dataset/sync_recorder.py --duration 60 --rate 10
    python face_dataset/sync_recorder.py --duration 0 --out session1   # 无限直到 Ctrl+C
    python face_dataset/sync_recorder.py --smoke                      # 只自检依赖，不连 VTS 不录音
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
import wave

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import numpy as np
import sounddevice as sd

from live2d.vtuber_studio_info import VTubeStudioAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_recorder")

PLUGIN_NAME = "FaceDatasetRecorder"
PLUGIN_DEV = "LLM_vtuber"


# ---------------------------------------------------------------------------
# 录音：回调线程按采样帧计数，把 int16 块写入 wav（帧号即音频时间轴）
# ---------------------------------------------------------------------------
class AudioSink:
    """把录音帧写入 wav，并记录开始时的 mono 时间。"""

    def __init__(self, wav_path: str, sample_rate: int, channels: int = 1):
        os.makedirs(os.path.dirname(os.path.abspath(wav_path)) or ".", exist_ok=True)
        self.wf = wave.open(wav_path, "wb")
        self.wf.setnchannels(channels)
        self.wf.setsampwidth(2)          # int16
        self.wf.setframerate(sample_rate)
        self.frame_count = 0
        self.t0_mono: float | None = None  # 录音开始瞬间的单调时钟

    def start(self) -> None:
        self.t0_mono = time.perf_counter()

    def write(self, indata: np.ndarray) -> None:
        self.wf.writeframes(indata.tobytes())
        self.frame_count += indata.shape[0]

    def close(self) -> None:
        self.wf.close()

    def audio_time_mono(self, frame_idx: int, sample_rate: int) -> float:
        """音频第 frame_idx 帧对应的 mono 时间（对齐关键）。"""
        assert self.t0_mono is not None
        return self.t0_mono + frame_idx / sample_rate


# ---------------------------------------------------------------------------
# VTS 参数采样：每帧打 t_mono
# ---------------------------------------------------------------------------
def flatten_params(data: dict, include_custom: bool) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for p in data.get("defaultParameters", []):
        items.append((p.get("name"), p.get("value", 0.0)))
    if include_custom:
        for p in data.get("customParameters", []):
            items.append((p.get("name"), p.get("value", 0.0)))
    return items


async def sample_loop(api, params_csv: str, rate: float, stop: asyncio.Event,
                      include_custom: bool, t0_audio_mono: float):
    """循环采样 VTS 参数写 CSV。t0_audio_mono 供时间对齐参考（记录进 meta 用）。"""
    interval = 1.0 / rate
    f = open(params_csv, "w", newline="", encoding="utf-8")
    writer = csv.writer(f)
    col_names: list[str] = []
    samples = 0
    try:
        while not stop.is_set():
            loop_t = time.perf_counter()
            try:
                resp = await api.get_tracking_parameters()
                ff = await api.get_face_found()
            except Exception as e:
                logger.warning(f"VTS 采样失败: {e}")
                await asyncio.sleep(interval)
                continue
            t_mono = time.perf_counter()
            face_found = bool((ff or {}).get("data", {}).get("found", False))
            data = (resp or {}).get("data") or {}
            items = flatten_params(data, include_custom)
            if not col_names:
                col_names = [n for n, _ in items]
                writer.writerow(["t_mono", "face_found"] + col_names)
            d = dict(items)
            row = [f"{t_mono:.6f}", int(face_found)]
            row += [f"{d.get(n, ''):.4f}" if d.get(n) is not None else "" for n in col_names]
            writer.writerow(row)
            f.flush()
            samples += 1
            if samples % max(1, int(rate)) == 0:
                logger.info(f"[参数] {samples} 帧 (face_found={face_found}) t_mono={t_mono:.3f}")
            await asyncio.sleep(max(0.0, interval - (time.perf_counter() - loop_t)))
    finally:
        f.close()
    return samples


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def run(args) -> None:
    out_prefix = args.out or f"face_session_{int(time.time())}"
    out_dir = os.path.dirname(os.path.abspath(out_prefix)) or "."
    os.makedirs(out_dir, exist_ok=True)
    params_csv = out_prefix + "_params.csv"
    wav_path = out_prefix + ".wav"
    meta_path = out_prefix + "_meta.json"

    # 1) 连接 VTS
    api = VTubeStudioAPI(host=args.host, port=args.port)
    if not await api.connect():
        logger.error("无法连接 VTS，请确认 VTube Studio 已启动且插件 API 已启用")
        return
    ok = await api.request_auth_token(PLUGIN_NAME, PLUGIN_DEV)
    if not ok:
        logger.error("VTS 插件认证失败（请在 VTS 内点击允许）")
        await api.disconnect()
        return

    # 2) 启动录音（先定录音起点，参数采样以同一 t0 为基准）
    sink = AudioSink(wav_path, args.sample_rate)
    sink.start()
    t0_audio_mono = sink.t0_mono

    def on_audio(indata, frames, time_info, status):
        if status:
            logger.warning(f"录音状态: {status}")
        sink.write(indata.copy())

    stream = sd.InputStream(samplerate=args.sample_rate, channels=1,
                            dtype="int16", callback=on_audio)
    stream.start()
    logger.info(f"录音已启动 → {wav_path}（t0_audio_mono={t0_audio_mono:.6f}）")

    # 3) 参数采样循环（Ctrl+C / 时长到达停止）
    stop = asyncio.Event()
    sampler = asyncio.create_task(
        sample_loop(api, params_csv, args.rate, stop, args.include_custom, t0_audio_mono))

    try:
        t_start = time.perf_counter()
        if args.duration > 0:
            await asyncio.sleep(args.duration)
        else:
            while True:                       # 无限：等待 Ctrl+C
                await asyncio.sleep(1)
                if not sampler.done() and (time.perf_counter() - t_start) % 10 < 1:
                    pass
    except asyncio.CancelledError:
        pass
    finally:
        stop.set()
        await sampler
        stream.stop()
        stream.close()
        sink.close()
        await api.disconnect()

    dur = time.perf_counter() - t_start
    meta = {
        "t0_audio_mono": t0_audio_mono,
        "sample_rate": args.sample_rate,
        "channels": 1,
        "dtype": "int16",
        "audio_frames": sink.frame_count,
        "audio_duration_s": round(sink.frame_count / args.sample_rate, 3),
        "record_duration_s": round(dur, 3),
        "vts_rate_hz": args.rate,
        "vts_host": args.host,
        "vts_port": args.port,
        "include_custom": args.include_custom,
        # 对齐方法说明（align.py 依赖）：
        #   音频任意时刻 mono = t0_audio_mono + t_audio（t_audio 为相对 wav 起点秒）
        #   VTS 参数帧按 t_mono 列与音频对齐
        "align_note": "mono = t0_audio_mono + t_audio",
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(f"完成。产物: {params_csv} / {wav_path} / {meta_path}")


def smoke() -> None:
    """依赖自检：不连 VTS、不录音。"""
    import importlib
    for m in ("sounddevice", "numpy", "websockets", "live2d.vtuber_studio_info"):
        importlib.import_module(m)
    print("smoke OK: 依赖齐备（sounddevice/numpy/websockets/VTubeStudioAPI）")
    print("下一步：python face_dataset/sync_recorder.py --duration 60")


def main(argv=None):
    ap = argparse.ArgumentParser(description="VTS 参数 + 录音 同步采集器（共享单调时间轴）")
    ap.add_argument("--host", default="localhost", help="VTS 主机（默认 localhost）")
    ap.add_argument("--port", type=int, default=8001, help="VTS API 端口（默认 8001）")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="录制秒数（0=无限直到 Ctrl+C，默认 0）")
    ap.add_argument("--rate", type=float, default=10.0, help="VTS 参数采样率 Hz（默认 10）")
    ap.add_argument("--out", default=None, help="输出前缀（默认 face_session_<ts>）")
    ap.add_argument("--sample-rate", type=int, default=16000, help="录音采样率（默认 16000）")
    ap.add_argument("--include-custom", action="store_true", help="同时收录插件自定义参数")
    ap.add_argument("--smoke", action="store_true", help="只做依赖自检")
    args = ap.parse_args(argv)
    if args.smoke:
        smoke()
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
