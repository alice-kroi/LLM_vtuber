"""V2 集成验证：Bilibili emitter + TTS listener + Live2D listener"""
import asyncio
import sys
sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.io.tts_listener import TTSListener
from LLM_v2.io.live2d_listener import Live2DListener
from LLM_v2.events import EVENTS


async def main():
    runtime = get_runtime()
    print(f"=== 集成验证 ===")
    print(f"persona: {runtime.persona.name}, "
          f"pad: {runtime.state.pad.dominant_quadrant()}, "
          f"E: {runtime.state.energy}\n")

    # 1. 启动 EventBus
    await runtime.bus.start()

    # 2. 启动 3 个 Brain
    will = WillEngineBrain(runtime)
    streamer = StreamerBrain(runtime)
    operator = OperatorBrain(runtime)
    await will.start()
    await streamer.start()
    await operator.start()
    print("[1] 3 brain 已启动")

    # 3. 启动 TTS + Live2D 监听器（不接实际设备，只验证注册）
    tts = TTSListener(runtime.bus)
    live2d = Live2DListener(runtime.bus, persona=runtime.persona)
    await tts.start()
    await live2d.start()
    print("[2] TTS + Live2D listener 已注册")

    # 4. 添加 BilibiliEmitter（不连真实 B 站，只验证导入）
    try:
        from LLM_v2.io.bilibili_emitter import BilibiliEmitter
        emitter = BilibiliEmitter(runtime.bus, forward_http=False)
        # 不真的 start()，只验证 import OK
        print("[3] BilibiliEmitter 导入成功（未连真实 B 站）")
    except Exception as e:
        print(f"[3] BilibiliEmitter 导入失败: {e}")

    await asyncio.sleep(0.5)

    # 5. 模拟全链路：Bilibili 弹幕 → Streamer → TTS + Live2D
    print("\n[4] 模拟全链路:")

    # 5.1 模拟弹幕输入
    print("\n  4.1 模拟弹幕: '帮我看看 B 站'")
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        "user": "测试观众",
        "text": "帮我看看 B 站",
    })
    await asyncio.sleep(3.0)

    # 5.2 模拟礼物
    print("\n  4.2 模拟礼物: '小星星'")
    await runtime.bus.emit(EVENTS.GIFT_RECEIVED, {
        "user": "测试观众",
        "gift": "小星星",
    })
    await asyncio.sleep(2.0)

    # 5.3 手动触发 Live2D 表情
    print("\n  4.3 触发 LIVE2D_EXPRESSION: excited")
    await runtime.bus.emit(EVENTS.LIVE2D_EXPRESSION, {"quadrant": "excited"})
    await asyncio.sleep(1.0)

    # 5.4 触发 Live2D 动作
    print("\n  4.4 触发 LIVE2D_MOTION: tap_head")
    await runtime.bus.emit(EVENTS.LIVE2D_MOTION, {"motion_id": "tap_head"})
    await asyncio.sleep(0.5)

    # 6. 验证 bus 统计
    print("\n[5] bus 统计:")
    stats = runtime.bus.stats()
    print(f"  emitted: {stats['emitted']}")
    print(f"  dispatched: {stats['dispatched']}")
    print(f"  errors: {stats['errors']}")
    print(f"  by_type:")
    for k, v in sorted(stats['by_type'].items()):
        print(f"    {k}: {v}")

    # 7. 关闭
    print("\n[6] 关闭...")
    await will.stop()
    await streamer.stop()
    await operator.stop()
    await runtime.bus.stop()

    print("\n=== 集成验证完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
