"""WebUI API 端点测试"""
import asyncio
import sys
import httpx

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.io.webui import create_webui_app


async def main():
    runtime = get_runtime()
    app = create_webui_app(runtime)

    import uvicorn
    config = uvicorn.Config(app=app, host='127.0.0.1', port=8091, log_level='error')
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(2)

    base = 'http://127.0.0.1:8091'

    print('=== WebUI API 测试 ===')

    # 1. 根路径
    r = await httpx.AsyncClient().get(f'{base}/')
    print(f'\n1. GET /: status={r.status_code}')
    root = r.json()
    print(f'   endpoints: {root["endpoints"]}')

    # 2. 状态查询
    r = await httpx.AsyncClient().get(f'{base}/api/state')
    s = r.json()
    print(f'\n2. GET /api/state:')
    print(f'   persona_name: {s["persona_name"]}')
    print(f'   pad.quadrant: {s["pad"]["quadrant"]}')
    print(f'   energy: {s["resources"]["energy"]}')

    # 3. Persona
    r = await httpx.AsyncClient().get(f'{base}/api/persona')
    p = r.json()
    print(f'\n3. GET /api/persona:')
    print(f'   name: {p["name"]}')
    print(f'   interests: {[i["name"] for i in p["interests"]]}')

    # 4. bus 统计
    r = await httpx.AsyncClient().get(f'{base}/api/stats')
    stats = r.json()
    print(f'\n4. GET /api/stats:')
    print(f'   emitted: {stats["emitted"]}')
    print(f'   running: {stats["running"]}')

    # 5. 手动发射
    r = await httpx.AsyncClient().post(
        f'{base}/api/emit',
        json={'event_type': 'bilibili_msg',
              'payload': {'user': 'webui_test', 'text': '通过 webui 发的弹幕'}}
    )
    print(f'\n5. POST /api/emit: status={r.status_code} body={r.json()}')
    await asyncio.sleep(0.5)

    # 6. 事件日志
    r = await httpx.AsyncClient().get(f'{base}/api/events?limit=10')
    e = r.json()
    print(f'\n6. GET /api/events: count={e["count"]}')
    for ev in e['events'][-3:]:
        print(f'   - {ev["event_type"]}: {ev["payload"]}')

    # 7. 应用 state 事件
    r = await httpx.AsyncClient().post(
        f'{base}/api/state/apply_event?event_type=test_webui&delta_P=0.1'
    )
    print(f'\n7. POST /api/state/apply_event: status={r.status_code}')

    # 8. 状态变化后查询
    r = await httpx.AsyncClient().get(f'{base}/api/state')
    s2 = r.json()
    print(f'   新的 PAD: P={s2["pad"]["P"]:.2f}')

    server.should_exit = True
    await server_task

    print('\n=== WebUI 验证完成 ===')


asyncio.run(main())
