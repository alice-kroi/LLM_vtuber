"""V2 OperatorBrain 通过 EventBus 调 launcher 工具"""
import asyncio
import sys

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.events import EVENTS


async def main():
    print('=' * 70)
    print('  V2 → EventBus → OperatorBrain → tool_registry → launcher_*')
    print('=' * 70)

    runtime = get_runtime()
    await runtime.bus.start()

    operator = OperatorBrain(runtime)

    results = []

    async def on_done(payload):
        if isinstance(payload, dict):
            results.append(payload)

    runtime.bus.on(EVENTS.OPERATOR_DONE, on_done)
    await operator.start()
    await asyncio.sleep(0.3)

    # 测试 1: launcher_status
    print('\n=== [1] 调 launcher_status ===')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'launcher_status',
        'args': {},
        'description': '查询当前窗口状态'
    })
    await asyncio.sleep(2.0)

    # 测试 2: launcher_start_all（一键启动）
    print('\n=== [2] 调 launcher_start_all ===')
    print('  注意：MuMu/ALAS/MAA 已经在跑，单实例保护会拒绝')
    print('  重点：测试 V2 链路是否能正常调用 launcher 工具')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'launcher_start_all',
        'args': {'instances': [0, 1]},
        'description': '一键启动 MuMu+ALAS+MAA'
    })
    await asyncio.sleep(30.0)  # 等长一点

    # 输出结果
    print('\n=== 结果 ===')
    for i, r in enumerate(results):
        tool = r.get('task', {}).get('tool', '?')
        desc = r.get('task', {}).get('description', '?')
        result = r.get('result', {})
        ok = result.get('success')
        print(f'\n  [{i+1}] {tool}')
        print(f'    desc: {desc}')
        print(f'    success: {ok}')
        if result.get('error'):
            print(f'    error: {result["error"][:200]}')
        elif result.get('result'):
            print(f'    result: {str(result["result"])[:200]}')

    await operator.stop()
    await runtime.bus.stop()
    print('\n=== 验证完成 ===')


asyncio.run(main())
