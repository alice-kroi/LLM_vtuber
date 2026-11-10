"""WebUI 完整端到端验证（启动 bus + WebUI）"""
import asyncio
import sys
import httpx

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.io.webui import create_webui_app


async def main():
    runtime = get_runtime()

    # 关键：必须先启动 bus 才能让 listener 收到事件
    await runtime.bus.start()

    app = create_webui_app(runtime)
    # 手动启动 pusher
    from LLM_v2.io.webui import EventPusher
    pusher = EventPusher(runtime)
    await pusher.start()

    import uvicorn
    config = uvicorn.Config(app=app, host='127.0.0.1', port=8092, log_level='error')
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(2)
    base = 'http://127.0.0.1:8092'

    print('=== WebUI 完整验证（bus 已启动）===')

    # 1. emit
    r = await httpx.AsyncClient().post(
        f'{base}/api/emit',
        json={'event_type': 'gift_received',
              'payload': {'user': 'test', 'gift': 'rocket'}}
    )
    print(f'\n1. POST /api/emit: {r.json()}')

    # 2. bilibili_msg
    r = await httpx.AsyncClient().post(
        f'{base}/api/emit',
        json={'event_type': 'bilibili_msg',
              'payload': {'user': '粉丝', 'text': '测试弹幕'}}
    )
    print(f'2. POST /api/emit: {r.json()}')

    # 等事件分发
    await asyncio.sleep(0.5)

    # 3. events 日志
    r = await httpx.AsyncClient().get(f'{base}/api/events?limit=20')
    e = r.json()
    print(f'\n3. /api/events count={e["count"]}')
    for ev in e['events'][-5:]:
        print(f'   - {ev["event_type"]}: {str(ev["payload"])[:60]}')

    # 4. stats
    r = await httpx.AsyncClient().get(f'{base}/api/stats')
    print(f'\n4. /api/stats: {r.json()["emitted"]} emitted')

    server.should_exit = True
    await task
    await runtime.bus.stop()

    print('\n=== 验证完成 ===')


asyncio.run(main())
