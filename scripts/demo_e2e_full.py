#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整端到端 demo：单角色三脑 + EDA + 真实 LLM + 真实 OS 工具
模拟一个直播间场景：
  - 观众发弹幕
  - 观众送礼物
  - AI 自主决定想干啥
  - 操作脑执行真实 OS 操作
  - TTS / Live2D 联动（不连真实设备，只验证事件流）
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.events import EVENTS


# 真实观众消息模板（不同时长、不同语气）
AUDIENCE_MESSAGES = [
    {"user": "资深粉丝", "text": "小鲤，今天有什么安排吗？"},
    {"user": "路人甲", "text": "刚来，主播好可爱"},
    {"user": "技术宅", "text": "我想学 PyTorch，能教我吗？"},
    {"user": "老粉", "text": "今天怎么看起来有点累啊"},
    {"user": "新人", "text": "主播主播，你会玩游戏吗？"},
    {"user": "金主", "gift": "小星星", "num": 1},
    {"user": "小鲤真爱粉", "text": "加油！看好你哦"},
    {"user": "粉丝团", "gift": "告白气球", "num": 10},
]


async def main():
    print("=" * 70)
    print("  完整端到端 demo：小鲤直播 V2 全链路")
    print("=" * 70)

    # 1. 加载 runtime
    runtime = get_runtime()
    print(f"\n[启动状态]")
    print(f"  人格：{runtime.persona.name}")
    print(f"  状态：{runtime.state.pad.dominant_quadrant()} "
          f"(P={runtime.state.pad.P:.2f}, A={runtime.state.pad.A:.2f}, D={runtime.state.pad.D:.2f})")
    print(f"  能量：{runtime.state.energy}/100  "
          f"社交需求：{runtime.state.social_need}/100  "
          f"好奇：{runtime.state.curiosity}/100")

    # 2. 启动 EventBus
    await runtime.bus.start()

    # 3. 启动 3 个 Brain
    print(f"\n[启动 Brain]")
    will = WillEngineBrain(runtime, decision_mode="hybrid")
    streamer = StreamerBrain(runtime)
    operator = OperatorBrain(runtime)
    await will.start()
    await streamer.start()
    await operator.start()
    print(f"  ✓ WillEngine (hybrid 模式)")
    print(f"  ✓ Streamer (真实 LLM)")
    print(f"  ✓ Operator (25 个真实 OS 工具)")

    # 4. 注册事件观察者
    print(f"\n[事件观察者已注册]")
    print(f"  (所有事件都会打印，便于追踪链路)")

    tts_calls = []
    live2d_calls = []
    decisions = []

    async def on_tts(payload):
        text = payload.get("text", "")[:60] if isinstance(payload, dict) else str(payload)[:60]
        tts_calls.append(payload)
        print(f"  [TTS] {text}")

    async def on_live2d(payload):
        live2d_calls.append(payload)
        q = payload.get("quadrant", "?") if isinstance(payload, dict) else "?"
        print(f"  [Live2D] 切表情：{q}")

    async def on_decided(payload):
        decisions.append(payload)
        if isinstance(payload, dict):
            print(f"  [WillEngine 决策] {payload.get('type')} → {payload.get('target')}")

    async def on_operator(payload):
        if isinstance(payload, dict):
            result = payload.get("result", {}) or {}
            print(f"  [Operator 完成] success={result.get('success')}, "
                  f"task={payload.get('task', {}).get('description', '?')[:50]}")

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    runtime.bus.on(EVENTS.LIVE2D_EXPRESSION, on_live2d)
    runtime.bus.on(EVENTS.WILL_ENGINE_DECIDED, on_decided)
    runtime.bus.on(EVENTS.OPERATOR_DONE, on_operator)

    await asyncio.sleep(0.5)

    # 5. 模拟场景
    print(f"\n{'='*70}")
    print(f"  场景模拟开始")
    print(f"{'='*70}")

    # 5.1 观众弹幕
    print(f"\n[场景 1] 观众弹幕进入直播间")
    for i, msg in enumerate(AUDIENCE_MESSAGES[:5]):
        print(f"\n  [{i+1}]", end=" ")
        if "gift" in msg:
            await runtime.bus.emit(EVENTS.GIFT_RECEIVED, msg)
        else:
            await runtime.bus.emit(EVENTS.BILIBILI_MSG, msg)
        await asyncio.sleep(2.5)  # 等 LLM 响应

    # 5.2 触发 Will Engine 自主决策
    print(f"\n[场景 2] 空闲 60 秒后 Will Engine 自主决策")
    print(f"  模拟 idle 状态...")
    from datetime import datetime, timedelta
    will.last_activity_at = datetime.now() - timedelta(seconds=60)
    # 临时改阈值强制触发
    will.IDLE_THRESHOLD = 1.0
    await will.heartbeat()
    await asyncio.sleep(3.0)

    # 5.3 自主决策后 Operator 执行
    if decisions:
        action = decisions[-1]
        print(f"\n[场景 3] Operator 执行自主决策")
        print(f"  决策：{action.get('type')} → {action.get('target')}")
        await runtime.bus.emit(EVENTS.DO_TASK, {
            "tool": "os_list_windows",
            "args": {},
            "description": "查看当前窗口",
        })
        await asyncio.sleep(2.0)

    # 5.4 礼物
    print(f"\n[场景 4] 粉丝送礼物")
    for i, msg in enumerate(AUDIENCE_MESSAGES[5:7]):
        print(f"\n  [{i+1}]", end=" ")
        if "gift" in msg:
            await runtime.bus.emit(EVENTS.GIFT_RECEIVED, msg)
        else:
            await runtime.bus.emit(EVENTS.BILIBILI_MSG, msg)
        await asyncio.sleep(2.0)

    # 6. 状态报告
    print(f"\n{'='*70}")
    print(f"  最终状态报告")
    print(f"{'='*70}")
    s = runtime.state
    print(f"\n[Persona State]")
    print(f"  PAD: {s.pad.dominant_quadrant()} "
          f"(P={s.pad.P:.2f}, A={s.pad.A:.2f}, D={s.pad.D:.2f})")
    print(f"  能量: {s.energy}/100")
    print(f"  专注: {s.focus}/100")
    print(f"  社交需求: {s.social_need}/100")
    print(f"  好奇: {s.curiosity}/100")

    print(f"\n[EventBus 统计]")
    stats = runtime.bus.stats()
    print(f"  emitted: {stats['emitted']}")
    print(f"  dispatched: {stats['dispatched']}")
    print(f"  errors: {stats['errors']}")
    print(f"  by_type:")
    for k, v in sorted(stats['by_type'].items(), key=lambda x: -x[1])[:15]:
        print(f"    {k}: {v}")

    print(f"\n[观察者统计]")
    print(f"  TTS 调用次数: {len(tts_calls)}")
    print(f"  Live2D 切表情次数: {len(live2d_calls)}")
    print(f"  Will Engine 决策次数: {len(decisions)}")

    # 7. 关闭
    print(f"\n[关闭序列]")
    await will.stop()
    await streamer.stop()
    await operator.stop()
    await runtime.bus.stop()

    print(f"\n{'='*70}")
    print(f"  ✓ 完整端到端 demo 完成")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
