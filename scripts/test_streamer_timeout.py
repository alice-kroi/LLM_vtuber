"""验证 Fix: StreamerBrain LLM 超时修复"""
import asyncio
import sys
import logging

sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.events import EVENTS


async def main():
    runtime = get_runtime()
    await runtime.bus.start()

    streamer = StreamerBrain(runtime, enable_rag=False)

    tts_events = []

    async def on_tts(payload):
        if isinstance(payload, dict):
            text = payload.get('text', '?')
            tts_events.append(text)
            print(f'  >>> TTS 收到 ({len(text)} 字符): {text[:100]}')

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    await streamer.start()
    await asyncio.sleep(1.0)

    print('\n=== 验证 1: 正常 LLM 调用（应拿到回复） ===')
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '验证',
        'text': '你好'
    })

    # 等 20 秒（足够 LLM 实际返回）
    for i in range(40):
        await asyncio.sleep(0.5)
        if tts_events:
            break

    elapsed = (i + 1) * 0.5
    print(f'  等了 {elapsed:.1f}s，收到 TTS 数: {len(tts_events)}')
    for t in tts_events:
        print(f'    > {t[:120]}')

    await streamer.stop()
    await runtime.bus.stop()
    print('\n=== 验证完成 ===')


asyncio.run(main())
