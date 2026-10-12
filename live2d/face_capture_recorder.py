#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VTuber Studio 面部捕捉参数录制器

从 VTS 持续读取"当前 tracking 参数"（摄像头/手机捕捉到的真实面部参数），
按帧写入 CSV，用于制作面部行为数据集。

设计要点（区别于 LIVE2D_PARAM_LOG）：
- LIVE2D_PARAM_LOG 记录的是"注入到 VTS 的参数"（AI 生成的表情），方向相反；
- 本录制器【只读】采样 get_tracking_parameters，不做任何 inject / idle，
  保证采集到的是 VTS 当前 tracking 的真实值（含你的面部捕捉参数）。

用法:
    python live2d/face_capture_recorder.py --duration 30 --rate 10
    python live2d/face_capture_recorder.py --duration 60 --rate 20 --out D:/data/my_face.csv

说明:
- 录制前请先在 VTS 里加载模型、开启摄像头追踪、对准人脸。
- 默认采样率 10Hz（VTS API 文档不建议 60+FPS 高频请求 InputParameterListRequest）。
- Ctrl+C 提前结束，CSV 已每帧 flush，数据不丢。
"""

import argparse
import asyncio
import csv
import logging
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("face_capture_recorder")


async def record(controller, out_path: str, duration: float, rate: float,
                 include_custom: bool, focus: list | None):
    """循环采样 VTS tracking 参数并写入 CSV。

    参数:
        controller: 已连接并初始化的 Live2DMain（仅用其 api）
        out_path:   CSV 输出路径
        duration:   录制时长（秒），<=0 表示无限录制直到 Ctrl+C
        rate:       采样率（Hz）
        include_custom: 是否一并收录插件自定义参数
        focus:      只录这些参数名（None = 全部）
    """
    interval = 1.0 / rate
    t0 = time.perf_counter()
    first = True
    param_names: list[str] = []

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    f = open(out_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(f)

    samples = 0
    face_found_total = 0
    last_report = 0.0
    try:
        while True:
            if duration > 0 and (time.perf_counter() - t0) >= duration:
                break
            loop_t = time.perf_counter()
            try:
                resp = await controller.api.get_tracking_parameters()
                ff_resp = await controller.api.get_face_found()
            except Exception as e:
                logger.warning(f"采样失败: {e}")
                await asyncio.sleep(interval)
                continue

            face_found = bool((ff_resp or {}).get("data", {}).get("found", False))
            data = (resp or {}).get("data") or {}
            params = {p.get("name"): p.get("value", 0.0)
                      for p in data.get("defaultParameters", [])}
            if include_custom:
                params.update({p.get("name"): p.get("value", 0.0)
                               for p in data.get("customParameters", [])})

            # 首帧确定列集合（保序）
            if first:
                names = list(params.keys())
                if focus:
                    names = [n for n in names if n in focus]
                param_names = names
                header = ["timestamp_ms", "elapsed_ms", "face_found"] + param_names
                writer.writerow(header)
                first = False

            elapsed = (time.perf_counter() - t0) * 1000.0
            row = [int(time.time() * 1000), f"{elapsed:.1f}", int(face_found)]
            row += [f"{params.get(n, ''):.4f}" if params.get(n) is not None else ""
                    for n in param_names]
            writer.writerow(row)
            f.flush()
            samples += 1
            if face_found:
                face_found_total += 1

            # 每秒打一次进度
            now = time.perf_counter()
            if now - last_report >= 1.0:
                last_report = now
                logger.info(
                    f"[录制中] {now - t0:.0f}s/{duration or '∞'}s  "
                    f"样本 {samples} (face_found {face_found_total}/{samples})  "
                    f"→ {out_path}")
            await asyncio.sleep(max(0.0, interval - (time.perf_counter() - loop_t)))
    except asyncio.CancelledError:
        logger.info("收到中断，保存已采集数据")
    finally:
        f.close()

    elapsed_total = time.perf_counter() - t0
    logger.info(
        f"录制结束：{samples} 帧 / {elapsed_total:.1f}s "
        f"（face_found 占比 {face_found_total / max(samples, 1):.0%}）→ {out_path}")
    return samples


async def main(argv: list | None = None):
    ap = argparse.ArgumentParser(description="VTuber Studio 面部捕捉参数录制器")
    ap.add_argument("--host", default="localhost", help="VTS 主机（默认 localhost）")
    ap.add_argument("--port", type=int, default=8001, help="VTS API 端口（默认 8001）")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="录制时长秒（<=0 无限直到 Ctrl+C，默认 30）")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="采样率 Hz（1-30，VTS 文档不建议高频，默认 10）")
    ap.add_argument("--out", default=None, help="CSV 输出路径（默认 logs/face_capture_<ts>.csv）")
    ap.add_argument("--include-custom", action="store_true",
                    help="同时收录插件自定义参数（默认只录 VTS 默认参数）")
    ap.add_argument("--focus", default=None,
                    help="只录这些参数，逗号分隔，如 FaceAngleX,FaceAngleY,MouthOpen,"
                         "EyeOpenLeft,EyeOpenRight,BrowLeft,BrowRight")
    args = ap.parse_args(argv)

    rate = max(1.0, min(30.0, args.rate))
    focus = [s.strip() for s in args.focus.split(",") if s.strip()] if args.focus else None
    if not args.out:
        log_dir = os.path.join(_PROJECT_ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        out_path = os.path.join(log_dir, f"face_capture_{int(time.time())}.csv")
    else:
        out_path = args.out

    from live2d.live2d_main import Live2DMain
    controller = Live2DMain(host=args.host, port=args.port)
    if not await controller.connect():
        logger.error("无法连接 VTS，请确认 VTube Studio 已启动且插件 API 已启用")
        return 1
    if not await controller.login():
        logger.error("VTS 插件认证失败（请在 VTS 内点击允许）")
        await controller.disconnect()
        return 1
    if not await controller.initialize():
        logger.error("VTS 初始化失败")
        await controller.disconnect()
        return 1

    logger.info(f"已连接 VTS（{args.host}:{args.port}），开始录制 → {out_path}")
    logger.info(f"参数范围：{'仅 VTS 默认参数' if not args.include_custom else '默认+自定义'}"
                + (f"，聚焦 {focus}" if focus else ""))
    try:
        await record(controller, out_path, args.duration, rate,
                     args.include_custom, focus)
    finally:
        await controller.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
