# ============================================================
# scripts/demo_v2_skeleton.py — V2 骨架验证 demo
# ============================================================
# 跑这个脚本：3 个 brain 同时存活，emit 一个事件看流转
# 预期看到：
#   1. 三脑启动
#   2. emit bilibili_msg → streamer 处理 → emit tts_speak + bilibili_reply
#   3. emit do_task → operator 处理 → emit operator_done
#   4. emit idle_tick → will_engine 处理 → emit will_engine_action
# ============================================================

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.events import EVENTS
from LLM_v2.core.event_bus import EventBus


async def main():
    print("=" * 60)
    print("V2 骨架验证：单角色三脑 + EDA")
    print("=" * 60)

    # 1. 加载 runtime
    runtime = get_runtime()
    print(f"\n[1] runtime loaded:")
    print(f"    name: {runtime.persona.name}")
    print(f"    pad: {runtime.state.pad.dominant_quadrant()}")
    print(f"    energy: {runtime.state.energy}")

    # 2. 添加一个观察者监听器（用于 demo 输出）
    async def on_tts(payload):
        print(f"    >>> TTS_SPEAK: {payload.get('text', '?') if isinstance(payload, dict) else payload}")

    async def on_reply(payload):
        print(f"    >>> BILIBILI_REPLY: {payload.get('text', '?') if isinstance(payload, dict) else payload}")

    async def on_operator_done(payload):
        result = payload.get('result', {}) if isinstance(payload, dict) else {}
        print(f"    >>> OPERATOR_DONE: success={result.get('success')}")

    async def on_will(payload):
        print(f"    >>> WILL_ENGINE_ACTION: {payload.get('type', '?')} -> {payload.get('target', '?')}")

    async def on_state(payload):
        print(f"    >>> STATE_UPDATED: {payload.get('event_type')}")

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    runtime.bus.on(EVENTS.BILIBILI_REPLY, on_reply)
    runtime.bus.on(EVENTS.OPERATOR_DONE, on_operator_done)
    runtime.bus.on(EVENTS.WILL_ENGINE_ACTION, on_will)
    runtime.bus.on(EVENTS.STATE_UPDATED, on_state)

    # 3. 启动总线 + 三个 brain
    await runtime.bus.start()
    will = WillEngineBrain(runtime)
    streamer = StreamerBrain(runtime)
    operator = OperatorBrain(runtime)
    await will.start()
    await streamer.start()
    await operator.start()

    print(f"\n[2] 3 个 brain 已启动:")
    print(f"    - will_engine (心跳 30s)")
    print(f"    - streamer (心跳 2s)")
    print(f"    - operator (心跳 3s)")
    print(f"    - bus 总监听数: {sum(runtime.bus.listener_count(e) for e in [EVENTS.BILIBILI_MSG, EVENTS.DO_TASK, EVENTS.WILL_ENGINE_ACTION, EVENTS.OPERATOR_DONE])}")

    await asyncio.sleep(0.5)  # 让监听器全部就绪

    # 4. 模拟事件流转
    print(f"\n[3] 模拟事件流:")

    print("\n  3.1 emit bilibili_msg:")
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        "user": "测试观众",
        "text": "我想学 PyTorch",
    })
    await asyncio.sleep(0.5)

    print("\n  3.2 emit gift_received:")
    await runtime.bus.emit(EVENTS.GIFT_RECEIVED, {
        "user": "测试观众",
        "gift": "小星星",
    })
    await asyncio.sleep(0.5)

    print("\n  3.3 emit do_task:")
    await runtime.bus.emit(EVENTS.DO_TASK, {
        "tool": "os_open_path",
        "args": {"path": "C:/"},
        "description": "打开我的电脑",
    })
    await asyncio.sleep(1.0)

    print("\n  3.4 emit will_engine_action:")
    await runtime.bus.emit(EVENTS.WILL_ENGINE_ACTION, {
        "type": "watch_video",
        "target": "bilibili_recommended",
        "reasoning": "demo test",
    })
    await asyncio.sleep(0.5)

    # 5. 验证 state 持久化
    print(f"\n[4] 最终 state:")
    s = runtime.state
    print(f"    pad: {s.pad.dominant_quadrant()} "
          f"(P={s.pad.P:.2f}, A={s.pad.A:.2f}, D={s.pad.D:.2f})")
    print(f"    energy: {s.energy}")
    print(f"    focus: {s.focus}")
    print(f"    social_need: {s.social_need}")
    print(f"    curiosity: {s.curiosity}")

    # 6. 验证事件总线统计
    print(f"\n[5] bus 统计:")
    stats = runtime.bus.stats()
    print(f"    emitted: {stats['emitted']}")
    print(f"    dispatched: {stats['dispatched']}")
    print(f"    errors: {stats['errors']}")
    print(f"    by_type: {dict(stats['by_type'])}")

    # 7. 关闭
    print(f"\n[6] 关闭...")
    await will.stop()
    await streamer.stop()
    await operator.stop()
    await runtime.bus.stop()

    print(f"\n=== V2 骨架验证完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
