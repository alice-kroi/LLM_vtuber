"""WillEngine API 决策模式验证"""
import asyncio
import sys
sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.events import EVENTS


async def main():
    runtime = get_runtime()
    print(f"persona: {runtime.persona.name}, "
          f"pad: {runtime.state.pad.dominant_quadrant()}, "
          f"E: {runtime.state.energy}\n")

    # 创建 WillEngine (API 模式)
    will = WillEngineBrain(runtime, decision_mode="api")

    # 监听决策事件
    async def on_decided(payload):
        print(f">>> WILL_ENGINE_DECIDED:")
        print(f"    type: {payload.get('type', '?')}")
        print(f"    target: {payload.get('target', '?')}")
        print(f"    reasoning: {payload.get('reasoning', '?')}")

    async def on_rest(payload):
        print(f">>> REST_REQUESTED: {payload}")

    runtime.bus.on(EVENTS.WILL_ENGINE_DECIDED, on_decided)
    runtime.bus.on(EVENTS.REST_REQUESTED, on_rest)

    await runtime.bus.start()
    await will.start()
    await asyncio.sleep(0.5)

    print("=== Test 1: API 决策（正常状态） ===")
    print("手动触发 _decide_api...")
    action = await will._decide_api(runtime.state, runtime.persona)
    print(f"\n决策结果: {json.dumps(action, ensure_ascii=False, indent=2) if action else 'None'}\n")

    print("=== Test 2: 模拟低能量触发强制休息 ===")
    runtime.state.energy = 5  # 模拟精疲力竭
    # 手动触发一次心跳（不等 30 秒）
    await will.heartbeat()
    await asyncio.sleep(0.5)
    # 恢复
    runtime.state.energy = 80

    print("\n=== Test 3: 触发 idle 心跳（API 模式决策） ===")
    # 模拟活动 → idle
    will.last_activity_at = datetime_minus(60)
    # 直接调用 heartbeat（不是 30 秒才触发）
    # 改 IDLE_THRESHOLD 临时
    will.IDLE_THRESHOLD = 1.0  # 临时改小
    await will.heartbeat()

    await asyncio.sleep(2.0)

    print("\n=== bus 统计 ===")
    stats = runtime.bus.stats()
    print(f"emitted: {stats['emitted']}, dispatched: {stats['dispatched']}")
    print(f"errors: {stats['errors']}")

    await will.stop()
    await runtime.bus.stop()
    print("\n=== 验证完成 ===")


def datetime_minus(seconds: float):
    from datetime import datetime, timedelta
    return datetime.now() - timedelta(seconds=seconds)


if __name__ == "__main__":
    asyncio.run(main())
