#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 参数效果对比测试脚本（交互式）

功能：
  1. 测试不同方向模板的幅度（通过 scale 参数缩放当前方向模板）
  2. 测试同方向微调幅度（连续多次发送同方向+随机偏移）
  3. 测试呼吸幅度（保持某方向不动，观察呼吸生命感）
  4. 预设对比：快速切换"原版/温和/自然/夸张"四种方案

使用方法：
  直接运行，根据菜单输入编号选择测试项。
  每次测试完成后按回车返回菜单。
"""

import asyncio
import math
import time
import sys
import os
import logging
import random

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d_main import Live2DMain, DIRECTION_TEMPLATES, VALID_DIRECTIONS, _ease_in_out_cubic

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TestParams")


# ============================================================
# 辅助函数
# ============================================================

def _scale_direction(direction: str, scale: float) -> dict:
    """将方向模板按 scale 缩放（0.5=一半幅度，2.0=两倍）"""
    tpl = DIRECTION_TEMPLATES[direction]
    return {pname: v * scale for pname, v in tpl.items()}


async def _move_to(ctl: Live2DMain, target: dict, duration: float = 1.5, label: str = ""):
    """从当前位置平滑移动到目标参数（复用 live2d_main.move 的缓动逻辑）"""
    ctl._ensure_core_params()
    start_time = time.perf_counter()
    start_params = {p: ctl.core_params.get(p, 0.0) for p in target}
    deltas = {p: target[p] - start_params[p] for p in target}

    logger.info(f"移动 {label}: deltas={ {k: round(v,2) for k,v in deltas.items()} }")

    frame_index = 0
    fps = 50
    step = 1.0 / fps
    async with ctl.operation_lock:
        while True:
            elapsed = time.perf_counter() - start_time
            if elapsed >= duration:
                break
            progress = _ease_in_out_cubic(elapsed / duration)

            # 呼吸叠加（小幅让过程自然）
            t_rel = time.time() - ctl.start_time
            breath_base = (
                0.5 * math.sin(2 * math.pi * t_rel / 9.0)
                + 0.3 * math.sin(2 * math.pi * t_rel / 14.0 + 1.3)
                + 0.2 * math.sin(2 * math.pi * t_rel / 23.0 + 2.7)
            )
            breath_angle = breath_base * 1.0
            breath_pos = breath_base * 0.03

            items = []
            for pname in target:
                base = start_params[pname] + deltas[pname] * progress
                if "Position" in pname:
                    items.append((pname, base + breath_pos))
                else:
                    items.append((pname, base + breath_angle))
            await ctl._inject_params(items, source=f"test:{label}")

            frame_index += 1
            next_tick = start_time + frame_index * step
            sleep_s = next_tick - time.perf_counter()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
            else:
                await asyncio.sleep(0)

        # 更新核心参数
        for pname, v in target.items():
            ctl.core_params[pname] = v
        # 最终帧
        final_items = [(pname, target[pname]) for pname in target]
        await ctl._inject_params(final_items, source=f"test_final:{label}")

    ctl.current_direction = ""  # 不追踪，避免影响下一次测试


async def _breathe_idle(ctl: Live2DMain, seconds: int, breath_angle: float, breath_pos: float):
    """保持 idle 呼吸状态 seconds 秒，观察生命感"""
    logger.info(f"呼吸观察 {seconds}s (angle_amp={breath_angle}, pos_amp={breath_pos})")
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        async with ctl.operation_lock:
            t_rel = time.time() - ctl.start_time
            breath_base = (
                0.5 * math.sin(2 * math.pi * t_rel / 9.0)
                + 0.3 * math.sin(2 * math.pi * t_rel / 14.0 + 1.3)
                + 0.2 * math.sin(2 * math.pi * t_rel / 23.0 + 2.7)
            )
            items = []
            for pname in ctl.core_params:
                if pname not in ("FaceAngleX", "FaceAngleY", "FaceAngleZ", "FacePositionX", "FacePositionY"):
                    continue
                core = ctl.core_params.get(pname, 0.0)
                if "Position" in pname:
                    items.append((pname, core + breath_base * breath_pos))
                else:
                    items.append((pname, core + breath_base * breath_angle))
            # 嘴巴参数
            mouth_param = ctl._resolve_mouth_param()
            if mouth_param and mouth_param in ctl.core_params:
                items.append((mouth_param, ctl.core_params[mouth_param]))
            await ctl._inject_params(items, source="test_idle")
        await asyncio.sleep(0.02)


# ============================================================
# 测试场景
# ============================================================

async def test_direction_scale(ctl: Live2DMain):
    """测试1：方向幅度对比"""
    print("\n" + "="*60)
    print("测试1：方向幅度对比（将依次发送 center → up 的不同缩放）")
    print("="*60)
    direction = input(f"请输入要测试的方向 {VALID_DIRECTIONS} (默认 up): ").strip() or "up"
    if direction not in DIRECTION_TEMPLATES:
        print(f"无效方向: {direction}")
        return

    scales = [0.3, 0.5, 0.75, 1.0, 1.25]  # 30% → 125% 对比
    duration = float(input("每次移动时长秒数 (默认 1.5): ").strip() or "1.5")
    hold_sec = int(input("每个幅度停留秒数用于观察 (默认 4): ").strip() or "4")

    # 先回到 center
    print(f"\n[0/6] 回到 center 基准...")
    await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="center")
    await _breathe_idle(ctl, 2, breath_angle=1.0, breath_pos=0.03)

    for i, scale in enumerate(scales):
        target = _scale_direction(direction, scale)
        tpl_desc = ", ".join(f"{k}={v:.1f}" for k, v in target.items())
        print(f"\n[{i+1}/6] 方向={direction}, 缩放={scale:.0%}, 目标值: {tpl_desc}")
        print(f"  观察要点：角度是否自然？会不会像'抬头看天'？")
        await _move_to(ctl, target, duration=duration, label=f"{direction} x{scale}")
        await _breathe_idle(ctl, hold_sec, breath_angle=1.0, breath_pos=0.03)

    # 回到 center
    print(f"\n[6/6] 回到 center...")
    await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="back_center")
    input("\n方向幅度对比完成，按回车返回菜单 > ")


async def test_same_direction_jitter(ctl: Live2DMain):
    """测试2：同方向微调幅度对比"""
    print("\n" + "="*60)
    print("测试2：同方向微调幅度（连续5次同一方向+随机偏移）")
    print("="*60)
    direction = input(f"请输入基准方向 {VALID_DIRECTIONS} (默认 up): ").strip() or "up"
    duration = float(input("每次移动时长秒数 (默认 1.2): ").strip() or "1.2")
    hold_sec = int(input("每次移动后停留秒数 (默认 3): ").strip() or "3")

    # 先到基准方向
    base_target = DIRECTION_TEMPLATES[direction]
    print(f"\n[0/6] 先到达基准方向 {direction}")
    await _move_to(ctl, base_target, duration=1.0, label=f"base_{direction}")
    await _breathe_idle(ctl, 2, breath_angle=1.0, breath_pos=0.03)

    # 不同微调幅度测试（围绕新基准 ±10° 上下扩展）
    jitter_angles = [4.0, 7.0, 10.0, 13.0, 16.0]  # 角度微调幅度 ±X°
    for i, jitter in enumerate(jitter_angles):
        # 生成随机偏移
        offset = {}
        for pname, v in base_target.items():
            if "Angle" in pname:
                offset[pname] = random.uniform(-jitter, jitter)
            elif "Position" in pname:
                offset[pname] = random.uniform(-jitter/30, jitter/30)  # 与角度同比例
        target = {pname: base_target[pname] + offset[pname] for pname in base_target}
        offset_desc = ", ".join(f"{k}:{v:+.1f}" for k, v in offset.items() if "Angle" in k)
        print(f"\n[{i+1}/5] 微调幅度=±{jitter}°, 偏移: {offset_desc}")
        print(f"  观察要点：是否能看出变化？会不会跳变太突兀？")
        await _move_to(ctl, target, duration=duration, label=f"jitter_{jitter}")
        # 更新 base 作为下一次起始点
        base_target = dict(target)
        await _breathe_idle(ctl, hold_sec, breath_angle=1.0, breath_pos=0.03)

    # 回到 center
    print(f"\n[6/6] 回到 center...")
    await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="back_center")
    input("\n同方向微调测试完成，按回车返回菜单 > ")


async def test_breathing_amplitude(ctl: Live2DMain):
    """测试3：呼吸幅度对比"""
    print("\n" + "="*60)
    print("测试3：呼吸幅度对比（停留在 center，观察不同呼吸幅度的生命感）")
    print("="*60)
    direction = input(f"停留方向 {VALID_DIRECTIONS} (默认 up): ").strip() or "up"
    hold_sec = int(input("每个幅度观察秒数 (默认 6): ").strip() or "6")

    # 先到停留方向（用 0.75 缩放更自然）
    target = _scale_direction(direction, 0.75)
    print(f"\n到达 {direction} (x0.75) 作为观察基准")
    await _move_to(ctl, target, duration=1.0, label=f"breath_base_{direction}")

    # 不同呼吸方案（围绕新基准 ±5°/±0.30 上下扩展）
    schemes = [
        ("温和", 2.0, 0.10),
        ("明显", 3.5, 0.20),
        ("当前代码 v4", 5.0, 0.30),
        ("加强", 7.5, 0.50),
        ("夸张", 10.0, 0.70),
    ]
    for i, (name, angle_amp, pos_amp) in enumerate(schemes):
        print(f"\n[{i+1}/5] 呼吸方案: {name}")
        print(f"  角度幅度=±{angle_amp}°,  位幅度=±{pos_amp}")
        print(f"  观察要点：有没有像真人一样微微晃动？会不会太机械？")
        await _breathe_idle(ctl, hold_sec, breath_angle=angle_amp, breath_pos=pos_amp)

    # 回到 center
    print(f"\n回到 center...")
    await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="back_center")
    input("\n呼吸幅度测试完成，按回车返回菜单 > ")


async def test_full_preset(ctl: Live2DMain):
    """测试4：完整预设方案（方向+呼吸+微调 整体效果）"""
    print("\n" + "="*60)
    print("测试4：4种预设完整方案对比")
    print("="*60)
    hold_sec = int(input("每个方向停留秒数 (默认 3): ").strip() or "3")

    # 注意：dir_scale=1.0 已对应"视线微偏"基准（FaceAngleY=8° / FaceAngleX=7°）
    # A档 = 当前代码的实际运行值。B/C/D围绕其做梯度对比
    presets = {
        "A. 当前代码（v4，呼吸+微调大幅提升）": {
            "dir_scale": 1.0,
            "jitter_angle": 10.0,
            "breath_angle": 5.0,
            "breath_pos": 0.30,
        },
        "B. 保守版（呼吸和微调稍小）": {
            "dir_scale": 1.0,
            "jitter_angle": 7.0,
            "breath_angle": 3.0,
            "breath_pos": 0.20,
        },
        "C. 加强版（更夸张的生命感）": {
            "dir_scale": 1.25,
            "jitter_angle": 12.0,
            "breath_angle": 7.0,
            "breath_pos": 0.50,
        },
        "D. 极限版（大动作+夸张呼吸）": {
            "dir_scale": 1.5,
            "jitter_angle": 15.0,
            "breath_angle": 10.0,
            "breath_pos": 0.80,
        },
    }

    # 循环方向序列（模拟真实对话的方向变化）
    demo_sequence = ["center", "right", "right", "up", "left", "down", "up"]  # 含重复方向

    for preset_name, cfg in presets.items():
        print("\n" + "-"*60)
        print(f"预设: {preset_name}")
        print(f"  方向缩放={cfg['dir_scale']:.0%}, 微调=±{cfg['jitter_angle']}°, "
              f"呼吸角度=±{cfg['breath_angle']}°, 呼吸位移=±{cfg['breath_pos']}")
        print(f"  将依次演示: {' → '.join(demo_sequence)}")
        input("  按回车开始演示 > ")

        last_dir = None
        for i, direction in enumerate(demo_sequence):
            base = _scale_direction(direction, cfg["dir_scale"])

            # 同方向加微调
            if direction == last_dir:
                for pname, v in base.items():
                    if "Angle" in pname:
                        base[pname] = v + random.uniform(-cfg["jitter_angle"], cfg["jitter_angle"])
                    elif "Position" in pname:
                        base[pname] = v + random.uniform(-cfg["jitter_angle"]/30, cfg["jitter_angle"]/30)
                label = f"{direction}*微"
            else:
                label = direction

            await _move_to(ctl, base, duration=1.5, label=label)
            last_dir = direction
            await _breathe_idle(ctl, hold_sec, breath_angle=cfg["breath_angle"], breath_pos=cfg["breath_pos"])

        # 回到 center
        await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="reset")
        await asyncio.sleep(1.0)
        print(f"  预设 {preset_name} 演示完成")

    input("\n4种预设对比完成，按回车返回菜单 > ")


async def test_custom_single(ctl: Live2DMain):
    """测试5：手动输入单个参数进行测试"""
    print("\n" + "="*60)
    print("测试5：手动输入参数值（快速验证特定数值）")
    print("="*60)
    print("参数说明:")
    print("  FaceAngleX: 左右摇头（-30左 ~ +30右，度）")
    print("  FaceAngleY: 上下抬头（-30低 ~ +30高，度）")
    print("  FaceAngleZ: 歪头（-30左歪 ~ +30右歪，度）")
    print("  FacePositionX: 左右平移（-1左 ~ +1右）")
    print("  FacePositionY: 上下平移（-1下 ~ +1上）")
    print("  输入空白值表示保持默认(0)")
    print()

    try:
        ax = float(input("FaceAngleX (默认 0): ").strip() or "0")
        ay = float(input("FaceAngleY (默认 0): ").strip() or "0")
        az = float(input("FaceAngleZ (默认 0): ").strip() or "0")
        px = float(input("FacePositionX (默认 0): ").strip() or "0")
        py = float(input("FacePositionY (默认 0): ").strip() or "0")
        duration = float(input("移动时长秒 (默认 1.5): ").strip() or "1.5")
        hold = int(input("停留观察秒数 (默认 5): ").strip() or "5")
    except ValueError:
        print("输入无效，取消")
        return

    target = {
        "FaceAngleX": ax, "FaceAngleY": ay, "FaceAngleZ": az,
        "FacePositionX": px, "FacePositionY": py,
    }
    print(f"\n目标参数: {target}")
    await _move_to(ctl, target, duration=duration, label="custom")
    await _breathe_idle(ctl, hold, breath_angle=1.5, breath_pos=0.05)

    # 回到 center
    print("回到 center...")
    await _move_to(ctl, DIRECTION_TEMPLATES["center"], duration=1.0, label="back_center")
    input("自定义测试完成，按回车返回菜单 > ")


# ============================================================
# 主菜单
# ============================================================

MENU = """
╔══════════════════════════════════════════════════════╗
║       Live2D 参数效果对比测试                         ║
╠══════════════════════════════════════════════════════╣
║  1. 方向幅度对比（up方向不同缩放，是否抬头看天？）     ║
║  2. 同方向微调幅度（±1/2/3/5/8度，变化是否明显？）   ║
║  3. 呼吸幅度对比（5种方案生命感对比）                 ║
║  4. 4种完整预设方案整体对比（推荐使用这个！）         ║
║  5. 手动输入单组参数快速验证                          ║
║                                                      ║
║  0. 退出                                             ║
╚══════════════════════════════════════════════════════╝
"""


async def main():
    logger.info("连接 VTube Studio ...")
    ctl = Live2DMain()

    if not await ctl.connect():
        logger.error("连接失败")
        return
    if not await ctl.login("TestParams", "Test"):
        logger.error("登录失败")
        return
    if not await ctl.initialize():
        logger.error("初始化失败")
        return
    logger.info("Live2D 初始化完成，可以开始测试")

    # 回到 center 起点
    ctl._ensure_core_params()
    async with ctl.operation_lock:
        items = [(pname, ctl.core_params[pname]) for pname in DIRECTION_TEMPLATES["center"]]
        await ctl._inject_params(items, source="init_center")
    await ctl.set_mouth_state(False)
    ctl.start_time = time.time()

    while True:
        print(MENU)
        choice = input("请选择测试项 > ").strip()
        if choice == "1":
            await test_direction_scale(ctl)
        elif choice == "2":
            await test_same_direction_jitter(ctl)
        elif choice == "3":
            await test_breathing_amplitude(ctl)
        elif choice == "4":
            await test_full_preset(ctl)
        elif choice == "5":
            await test_custom_single(ctl)
        elif choice == "0":
            break
        else:
            print("无效选项")

    logger.info("断开连接")
    await ctl.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
