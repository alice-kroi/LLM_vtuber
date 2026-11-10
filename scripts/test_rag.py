"""RAG 集成验证"""
import asyncio
import sys

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.core.rag_adapter import get_rag
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.events import EVENTS


async def main():
    print('=== RAG 集成验证 ===\n')

    runtime = get_runtime()
    await runtime.bus.start()

    # 1. 测试 RAG 适配器本身
    print('1. 测试 RAG 适配器（独立）')
    rag = get_rag(top_k=3)
    try:
        results = await rag.retrieve("PyTorch 怎么学", top_k=3)
        print(f'   RAG 检索结果数: {len(results)}')
        if results:
            for r in results[:2]:
                print(f'   - score={r["score"]:.2f}: {r["content"][:60]}...')
        else:
            print('   （无结果，可能 Milvus 未连接或无数据）')
    except Exception as e:
        print(f'   RAG 失败（预期：Milvus 可能未启）: {e}')

    print()

    # 2. 测试 build_context
    print('2. 测试 build_context（拼装到 prompt）')
    try:
        ctx = await rag.build_context("测试查询", top_k=2)
        if ctx:
            print(f'   上下文长度: {len(ctx)} 字符')
            print(f'   内容预览: {ctx[:200]}')
        else:
            print('   （无上下文）')
    except Exception as e:
        print(f'   失败: {e}')

    print()

    # 3. 测试 StreamerBrain 集成 RAG
    print('3. 测试 StreamerBrain (集成 RAG)')
    streamer = StreamerBrain(runtime, enable_rag=True)

    async def on_tts(payload):
        if isinstance(payload, dict):
            text = payload.get("text", "")
            print(f'   [TTS] {text[:120]}...')

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    await streamer.start()
    await asyncio.sleep(0.5)

    # 4. 模拟弹幕 → 触发 RAG + LLM
    print('\n4. 模拟弹幕 → RAG + LLM')
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '测试观众',
        'text': '我想学 PyTorch, 教教我'
    })
    await asyncio.sleep(4.0)

    # 5. 验证关闭
    print('\n5. 关闭')
    await streamer.stop()
    await runtime.bus.stop()
    print('   OK')

    print('\n=== 验证完成 ===')


if __name__ == '__main__':
    asyncio.run(main())
