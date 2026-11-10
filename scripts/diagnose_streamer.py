"""StreamerBrain 排查 - 检查 listener 是否注册"""
import asyncio
import sys
import logging

sys.path.insert(0, '.')

# 强制日志输出
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    force=True,
)

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.events import EVENTS


async def main():
    runtime = get_runtime()

    print('\n=== [A] bus 启动前 ===')
    print(f'  bus.running: {runtime.bus._running}')
    print(f'  listeners(bilibili_msg): {runtime.bus.listener_count(EVENTS.BILIBILI_MSG)}')

    await runtime.bus.start()

    print('\n=== [B] bus 启动后 ===')
    print(f'  bus.running: {runtime.bus._running}')
    print(f'  listeners(bilibili_msg): {runtime.bus.listener_count(EVENTS.BILIBILI_MSG)}')

    streamer = StreamerBrain(runtime, enable_rag=False)
    print('\n=== [C] streamer 创建后 ===')
    print(f'  streamer._running: {streamer._running}')

    await streamer.start()

    # 等 1 秒让 setup() 执行
    await asyncio.sleep(1.0)

    print('\n=== [D] streamer.start() 后等 1 秒 ===')
    print(f'  streamer._running: {streamer._running}')
    print(f'  listeners(bilibili_msg): {runtime.bus.listener_count(EVENTS.BILIBILI_MSG)}')

    # 测试 emit
    print('\n=== [E] 手动 emit + 等 8 秒 ===')

    async def on_any(payload):
        print(f'  >>> 收到事件: {payload}')

    runtime.bus.on(EVENTS.TTS_SPEAK, on_any)
    runtime.bus.on(EVENTS.STATE_UPDATED, on_any)
    print(f'  新加监听器后: tts_listeners={runtime.bus.listener_count(EVENTS.TTS_SPEAK)}')

    print('\n  [E.1] emit bilibili_msg')
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '排查',
        'text': '你好'
    })
    print(f'  emit 完成')

    # 等 8 秒
    for i in range(16):
        await asyncio.sleep(0.5)
        s = runtime.bus.stats()
        print(f'  [{i*0.5:.1f}s] emitted={s["emitted"]} dispatched={s["dispatched"]} errors={s["errors"]}')

    await streamer.stop()
    await runtime.bus.stop()

    print('\n=== 完成 ===')


asyncio.run(main())
