"""LLM 对话失败原因排查"""
import asyncio
import os
import sys
import time

sys.path.insert(0, '.')


async def check_environment():
    """阶段 1: 检查环境配置"""
    print('=' * 70)
    print('  LLM 对话失败排查')
    print('=' * 70)

    print('\n=== [1] 环境变量检查 ===')
    api_key = os.getenv('Doubao_API_KEY')
    api_url = os.getenv('Doubao_API_URL')
    print(f'  Doubao_API_KEY: {"设置" if api_key else "未设置"} '
          f'(长度={len(api_key) if api_key else 0})')
    print(f'  Doubao_API_URL: {api_url or "未设置"}')

    print('\n=== [2] config.ini 检查 ===')
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read('config.ini', encoding='utf-8')
    if cfg.has_section('model'):
        model = cfg.get('model', 'model', fallback=None)
        print(f'  [model] model = {model}')
    else:
        print('  [model] 节不存在！')


async def check_llm_adapter():
    """阶段 2: LLMAdapter 单测"""
    print('\n=== [3] LLMAdapter 单测 ===')
    from LLM_v2.core.llm_adapter import get_llm

    llm = get_llm()
    print(f'  model: {llm.model}')
    print(f'  api_url: {llm.api_url}')
    print(f'  api_key 设置: {bool(llm.api_key)}')

    # 单条简单调用
    print('\n=== [4] 简单 LLM 调用测试 ===')
    start = time.time()
    try:
        result = await llm.chat_simple(
            system_prompt='你是小鲤。',
            user_text='用一句话介绍你自己'
        )
        elapsed = time.time() - start
        print(f'  耗时: {elapsed:.2f}s')
        print(f'  结果长度: {len(result)}')
        print(f'  结果前 200 字符: {result[:200]}')
    except Exception as e:
        elapsed = time.time() - start
        print(f'  异常 ({elapsed:.2f}s): {type(e).__name__}: {e}')


async def check_chat_model_direct():
    """阶段 3: 直接调用 chat_model 验证"""
    print('\n=== [5] 直接调 LLM/chat_model.doubao_chat_node ===')
    from LLM.chat_model import doubao_chat_node

    state = {
        'messages': [{'role': 'user', 'content': '你好'}],
        'system_prompt': '你是小鲤。',
        'model': 'doubao-seed-2-1-turbo-260628',
        'temperature': 0.7,
        'max_tokens': 100,
    }

    start = time.time()
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, doubao_chat_node, state, None
        )
        elapsed = time.time() - start
        print(f'  耗时: {elapsed:.2f}s')
        print(f'  error: {result.get("error")}')
        print(f'  response: {result.get("response", "")[:200]}')
        print(f'  tokens: {result.get("tokens_used")}')
    except Exception as e:
        elapsed = time.time() - start
        print(f'  异常 ({elapsed:.2f}s): {type(e).__name__}: {e}')


async def check_streamer_brain_full_chain():
    """阶段 4: StreamerBrain 完整链路"""
    print('\n=== [6] StreamerBrain 完整链路 ===')
    from LLM_v2.core.persona_runtime import get_runtime
    from LLM_v2.brains.streamer import StreamerBrain
    from LLM_v2.events import EVENTS

    runtime = get_runtime()
    await runtime.bus.start()

    streamer = StreamerBrain(runtime, enable_rag=False)

    events = []

    async def on_tts(payload):
        events.append(('tts', payload))

    async def on_state(payload):
        events.append(('state', payload.get('event_type')))

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    runtime.bus.on(EVENTS.STATE_UPDATED, on_state)

    await streamer.start()
    print(f'  streamer 已启动 (L LM={bool(streamer._llm.api_key)})')
    await asyncio.sleep(0.5)

    print('\n  发送弹幕: "你好"')
    start = time.time()
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '排查测试',
        'text': '你好'
    })

    # 等 5 秒
    for i in range(10):
        await asyncio.sleep(0.5)
        if events:
            print(f'  [{i*0.5:.1f}s] 已收到 {len(events)} 个事件')
            if len(events) >= 2:
                break

    elapsed = time.time() - start
    print(f'\n  总耗时: {elapsed:.2f}s')
    print(f'  收到事件: {len(events)} 个')

    # 分析事件
    print('\n  事件清单:')
    for evt_type, payload in events[:10]:
        if evt_type == 'tts':
            text = payload.get('text', '?')[:80]
            print(f'    [TTS] {text}')
        elif evt_type == 'state':
            print(f'    [STATE] {payload}')

    await streamer.stop()
    await runtime.bus.stop()


async def check_doubao_endpoint():
    """阶段 5: 直接 HTTP 测试 API 端点"""
    print('\n=== [7] 直接 HTTP 测试 ===')
    import aiohttp

    api_key = os.getenv('Doubao_API_KEY')
    api_url = os.getenv('Doubao_API_URL', 'https://ark.cn-beijing.volces.com/api/v3')

    if not api_key:
        print('  无 API_KEY，跳过')
        return

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': 'doubao-seed-2-1-turbo-260628',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 50,
    }

    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{api_url}/chat/completions',
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                elapsed = time.time() - start
                print(f'  HTTP {resp.status} ({elapsed:.2f}s)')
                text = await resp.text()
                print(f'  响应: {text[:300]}')
    except asyncio.TimeoutError:
        print(f'  超时 ({time.time()-start:.1f}s)')
    except Exception as e:
        print(f'  异常: {type(e).__name__}: {e}')


async def main():
    await check_environment()
    await check_llm_adapter()
    await check_chat_model_direct()
    await check_doubao_endpoint()
    await check_streamer_brain_full_chain()
    print('\n' + '=' * 70)
    print('  排查完成')
    print('=' * 70)


if __name__ == '__main__':
    asyncio.run(main())
