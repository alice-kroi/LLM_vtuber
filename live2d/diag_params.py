#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 参数诊断脚本 v2

验证：
  1. move() 发送帧数是否足够（期望 ~50帧/秒 × duration）
  2. 帧值是否平滑过渡（ease-in-out 曲线，非跳变）
  3. move 完成后 idle_movement 是否维持 core_params + 呼吸（非归零）
  4. 呼吸幅度是否可见
"""

import asyncio
import logging
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from live2d.live2d_main import Live2DMain, DIRECTION_TEMPLATES, _ease_in_out_cubic

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("diag")


SAMPLE_PARAMS = [
    "FaceAngleX", "FaceAngleY", "FaceAngleZ",
    "FacePositionX", "FacePositionY",
]


async def sample_loop(controller: Live2DMain, samples: list, stop_event: asyncio.Event):
    """采样 VTS 返回的实际参数值。持锁避免与 inject 并发。"""
    t0 = time.perf_counter()
    while not stop_event.is_set():
        try:
            async with controller.operation_lock:
                resp = await controller.api.get_tracking_parameters()
            if resp and "data" in resp:
                data = resp["data"]
                values = {}
                for key in ("defaultParameters", "customParameters"):
                    for p in data.get(key, []):
                        name = p.get("name")
                        if name in SAMPLE_PARAMS:
                            values[name] = p.get("value", 0)
                samples.append((time.perf_counter() - t0, dict(values)))
        except Exception as e:
            logger.warning(f"采样失败: {e}")
        await asyncio.sleep(0.03)


async def main():
    controller = Live2DMain(host="localhost", port=8001)
    if not await controller.connect():
        print("连接失败")
        return
    if not await controller.login():
        print("登录失败")
        return
    if not await controller.initialize():
        print("初始化失败")
        return

    # 关键：设置 running=True，让 idle_movement 能运行
    controller.running = True

    print("=== 初始化完成，准备采样 ===")
    # 先回中心静置 1 秒
    await controller.move_to_direction("center", 0.8)
    await asyncio.sleep(1.0)

    # 记录 move 内部发送的帧数据（通过 monkey-patch _inject_params）
    move_frames: list[tuple[float, dict]] = []
    original_inject = controller._inject_params

    async def patched_inject(items):
        move_frames.append((time.perf_counter(), {name: val for name, val in items}))
        await original_inject(items)

    controller._inject_params = patched_inject

    samples: list = []
    stop_event = asyncio.Event()

    sampler = asyncio.create_task(sample_loop(controller, samples, stop_event))
    idler = asyncio.create_task(controller.idle_movement())

    print("=== 开始执行 move_to_direction('right', 2.0) ===")
    t_start = time.perf_counter()
    await controller.move_to_direction("right", 2.0)
    t_move_done = time.perf_counter() - t_start
    print(f"=== move 完成，耗时 {t_move_done:.3f}s ===")

    # 继续采样 3 秒，观察 idle 呼吸
    await asyncio.sleep(3.0)
    stop_event.set()

    # 恢复原始 inject
    controller._inject_params = original_inject
    controller.running = False

    sampler.cancel()
    idler.cancel()
    try:
        await sampler
    except asyncio.CancelledError:
        pass
    try:
        await idler
    except asyncio.CancelledError:
        pass

    await controller.disconnect()

    # === 分析 move 帧数据 ===
    print(f"\n{'='*80}")
    print(f"=== move 内部帧数据分析 ===")
    print(f"{'='*80}")
    print(f"总帧数: {len(move_frames)} (期望 ~{int(2.0 * 50)})")

    if len(move_frames) > 2:
        t0_frame = move_frames[0][0]
        print(f"\n时间(ms)  | progress | ease    | AngleX    | AngleY    | AngleZ    | PosX      | PosY")
        print("-" * 95)
        # 每隔 N 帧打印一次
        step = max(1, len(move_frames) // 25)
        target = DIRECTION_TEMPLATES["right"]
        for i in range(0, len(move_frames), step):
            t, vals = move_frames[i]
            elapsed_ms = (t - t0_frame) * 1000
            progress_raw = min(elapsed_ms / 2000.0, 1.0)
            ease = _ease_in_out_cubic(progress_raw)
            ax = vals.get("FaceAngleX", 0)
            ay = vals.get("FaceAngleY", 0)
            az = vals.get("FaceAngleZ", 0)
            px = vals.get("FacePositionX", 0)
            py = vals.get("FacePositionY", 0)
            print(f"{elapsed_ms:7.1f}  | {progress_raw:7.3f}  | {ease:6.3f}  | {ax:+9.3f} | {ay:+9.3f} | {az:+9.3f} | {px:+9.4f} | {py:+9.4f}")

        # 验证过渡平滑性
        print(f"\n=== 过渡平滑性分析 ===")
        ax_values = [f[1].get("FaceAngleX", 0) for f in move_frames]
        if len(ax_values) >= 3:
            # 检查是否单调递增（允许呼吸导致的微小波动）
            increasing = sum(1 for i in range(1, len(ax_values)) if ax_values[i] >= ax_values[i-1] - 0.5)
            print(f"FaceAngleX 起始: {ax_values[0]:+.3f}")
            print(f"FaceAngleX 终止: {ax_values[-1]:+.3f} (目标: {target['FaceAngleX']:+.3f})")
            # 检查中间值
            mid = ax_values[len(ax_values)//2]
            print(f"FaceAngleX 中段: {mid:+.3f} (期望 ~{target['FaceAngleX']*0.5:+.3f})")
            # 检查是否有跳变（相邻帧差值过大）
            max_jump = max(abs(ax_values[i] - ax_values[i-1]) for i in range(1, len(ax_values)))
            print(f"相邻帧最大跳变: {max_jump:.3f} 度 (平滑应 < 1.0)")
            if max_jump < 2.0 and mid > 2.0 and mid < target['FaceAngleX'] - 2.0:
                print("  >> 过渡平滑性: PASS (ease-in-out 曲线生效)")
            else:
                print("  >> 过渡平滑性: 需检查")

    # === 分析 VTS 采样数据（move 后的 idle 呼吸）===
    print(f"\n{'='*80}")
    print(f"=== VTS 采样数据分析（move 后 idle 呼吸）===")
    print(f"{'='*80}")
    print(f"共采集 {len(samples)} 个样本")

    # move 后的样本（t > t_move_done + 0.2）
    t_move_abs = t_start + 0  # 相对时间
    after_samples = [(t, v) for t, v in samples if t > 1.0 + t_move_done + 0.2]
    print(f"move 后样本数: {len(after_samples)}")

    if after_samples:
        print(f"\n时间(s)   | AngleX    | AngleY    | AngleZ    | PosX      | PosY")
        print("-" * 78)
        step2 = max(1, len(after_samples) // 20)
        for i in range(0, len(after_samples), step2):
            t, vals = after_samples[i]
            ax = vals.get("FaceAngleX", 0)
            ay = vals.get("FaceAngleY", 0)
            az = vals.get("FaceAngleZ", 0)
            px = vals.get("FacePositionX", 0)
            py = vals.get("FacePositionY", 0)
            print(f"{t:7.3f}  | {ax:+9.4f} | {ay:+9.4f} | {az:+9.4f} | {px:+9.4f} | {py:+9.4f}")

        # 呼吸波动分析
        print(f"\n=== 呼吸波动分析 ===")
        ax_vals = [v.get("FaceAngleX", 0) for _, v in after_samples]
        ay_vals = [v.get("FaceAngleY", 0) for _, v in after_samples]
        if ax_vals:
            ax_range = max(ax_vals) - min(ax_vals)
            ay_range = max(ay_vals) - min(ay_vals)
            ax_mean = sum(ax_vals) / len(ax_vals)
            print(f"FaceAngleX 均值: {ax_mean:+.4f} (期望 ~{target['FaceAngleX']:+.3f} + 呼吸)")
            print(f"FaceAngleX 波动范围: {ax_range:.4f} 度 (呼吸幅度，v1=0.000)")
            print(f"FaceAngleY 波动范围: {ay_range:.4f} 度")
            if ax_range > 0.05:
                print("  >> 呼吸生效: PASS (有可见波动)")
            else:
                print("  >> 呼吸生效: FAIL (无波动)")
            if abs(ax_mean - target['FaceAngleX']) < 2.0:
                print("  >> 位置维持: PASS (保持在目标方向)")
            else:
                print(f"  >> 位置维持: FAIL (均值 {ax_mean:.4f} 偏离目标 {target['FaceAngleX']:.3f})")
    else:
        print("无 move 后样本，无法分析呼吸")


if __name__ == "__main__":
    asyncio.run(main())
