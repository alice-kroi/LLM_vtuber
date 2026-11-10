"""V2 核心能力综合测试：电脑操作 + 记忆保存 + LLM 对话"""
import asyncio
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, '.')

from LLM_v2.core.persona_runtime import get_runtime
from LLM_v2.brains.will_engine import WillEngineBrain
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.brains.operator import OperatorBrain
from LLM_v2.brains.reflection import ReflectionBrain
from LLM_v2.core.rag_adapter import get_rag
from LLM_v2.events import EVENTS


async def test_os_tools(runtime):
    """测试 1: 电脑操作能力（多个真实 OS 工具）"""
    print('\n=== Test 1: 电脑操作能力 ===')
    operator = OperatorBrain(runtime)

    results = []

    async def on_done(payload):
        results.append(payload)

    runtime.bus.on(EVENTS.OPERATOR_DONE, on_done)

    await runtime.bus.start()
    await operator.start()
    await asyncio.sleep(0.3)

    # 测试 1.1: 列出当前窗口
    print('\n  1.1 os_list_windows')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_list_windows', 'args': {},
        'description': '列出所有窗口'
    })
    await asyncio.sleep(1.0)

    # 测试 1.2: 列出 Documents 目录
    print('  1.2 os_list_directory')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_list_directory',
        'args': {'path': 'C:/Users/Administrator/Documents'},
        'description': '列出 Documents 目录'
    })
    await asyncio.sleep(1.5)

    # 测试 1.3: 创建文件
    test_path = 'C:/Users/Administrator/Documents/llmvt_test_file.txt'
    print(f'  1.3 os_create_file -> {test_path}')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_create_file',
        'args': {'path': test_path, 'content': 'Hello from V2 test!\n这是 V2 测试文件。'},
        'description': '创建测试文件'
    })
    await asyncio.sleep(1.0)

    # 测试 1.4: 读文件
    print('  1.4 os_read_file')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_read_file',
        'args': {'path': test_path},
        'description': '读测试文件'
    })
    await asyncio.sleep(1.0)

    # 测试 1.5: 运行命令
    print('  1.5 os_run_command (python version)')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_run_command',
        'args': {'cmd': 'python --version'},
        'description': '检查 Python 版本'
    })
    await asyncio.sleep(2.0)

    # 测试 1.6: 编辑文件
    print('  1.6 os_edit_file')
    await runtime.bus.emit(EVENTS.DO_TASK, {
        'tool': 'os_edit_file',
        'args': {
            'path': test_path,
            'search_text': 'Hello from V2 test!',
            'new_text': 'Hello from V2 (edited)!'
        },
        'description': '编辑测试文件'
    })
    await asyncio.sleep(1.0)

    # 总结
    success_count = sum(
        1 for r in results if r.get('result', {}).get('success')
    )
    print(f'\n  结果: {success_count}/{len(results)} 成功')

    # 清理
    if os.path.exists(test_path):
        os.remove(test_path)
        print(f'  清理: 已删除 {test_path}')

    await operator.stop()


async def test_memory_persistence(runtime):
    """测试 2: 记忆保存能力（state 持久化）"""
    print('\n=== Test 2: 记忆保存能力 ===')

    print(f'\n  初始 state: E={runtime.state.energy}, F={runtime.state.focus}')

    # 2.1 应用事件修改 state
    print('\n  2.1 修改 state（连续 3 个事件）')
    runtime.apply_event('webui_test', delta_P=0.05, delta_energy=10)
    runtime.apply_event('chat_received', delta_social=-5)
    runtime.apply_event('task_done', delta_P=0.1, delta_focus=-3)

    s = runtime.state
    print(f'  修改后: P={s.pad.P:.2f}, E={s.energy}, F={s.focus}, S={s.social_need}')

    # 2.2 持久化到 sqlite
    print('\n  2.2 持久化到 sqlite (自动)')
    print(f'  sqlite 路径: persona/state.db')

    # 2.3 模拟重启：重新创建 runtime（不直接验证，等下个 test 验证）
    # 这里我们验证数据被持久化
    import sqlite3
    with sqlite3.connect('persona/state.db') as conn:
        row = conn.execute(
            'SELECT pad_P, pad_A, pad_D, energy, focus, social_need, curiosity '
            'FROM persona_state WHERE id=1'
        ).fetchone()
        print(f'  sqlite 实际值: P={row[0]:.2f}, A={row[1]:.2f}, D={row[2]:.2f}, '
              f'E={row[3]}, F={row[4]}, S={row[5]}, C={row[6]}')

    # 2.4 验证 state_events 表（事件历史）
    with sqlite3.connect('persona/state.db') as conn:
        events = conn.execute(
            'SELECT event_type, delta_P, delta_energy FROM state_events '
            'ORDER BY id DESC LIMIT 5'
        ).fetchall()
        print(f'  最近 5 个事件:')
        for e in events:
            print(f'    - {e[0]}: ΔP={e[1]:.2f}, ΔE={e[2]}')


async def test_rag_retrieval(runtime):
    """测试 3: RAG 检索能力"""
    print('\n=== Test 3: RAG 记忆检索 ===')

    rag = get_rag(top_k=3)
    print(f'  RAG 初始化: {rag._initialized}')

    # 3.1 单条查询
    print('\n  3.1 查询 "PyTorch 怎么学"')
    try:
        results = await rag.retrieve('PyTorch 怎么学', top_k=3)
        print(f'  检索到 {len(results)} 条:')
        for r in results[:3]:
            print(f'    - score={r["score"]:.2f} rel={r["relevance"]:.2f}: '
                  f'{r["content"][:60]}...')
    except Exception as e:
        print(f'  RAG 检索失败（可能 Milvus 未启）: {e}')

    # 3.2 拼装到 prompt
    print('\n  3.2 build_context')
    try:
        ctx = await rag.build_context('深度学习', top_k=2)
        if ctx:
            print(f'  上下文长度: {len(ctx)} 字符')
            print(f'  内容预览: {ctx[:150]}')
        else:
            print('  （无上下文）')
    except Exception as e:
        print(f'  失败: {e}')


async def test_llm_conversation(runtime):
    """测试 4: LLM 对话能力"""
    print('\n=== Test 4: LLM 真实对话 ===')

    streamer = StreamerBrain(runtime, enable_rag=False)

    responses = []

    async def on_tts(payload):
        if isinstance(payload, dict):
            responses.append(payload.get('text', ''))

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    await streamer.start()
    await asyncio.sleep(0.5)

    # 4.1 测试对话
    print('\n  4.1 发送弹幕')
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '核心测试',
        'text': '你好，简单介绍下你自己',
    })
    await asyncio.sleep(3.0)

    print(f'  LLM 响应数: {len(responses)}')
    for r in responses:
        if r:
            print(f'  > {r[:120]}')

    await streamer.stop()


async def main():
    print('=' * 70)
    print('  V2 核心能力综合测试')
    print('=' * 70)

    runtime = get_runtime()

    # 测试 1: 电脑操作
    await test_os_tools(runtime)

    # 测试 2: 记忆保存
    await test_memory_persistence(runtime)

    # 测试 3: RAG 检索
    await test_rag_retrieval(runtime)

    # 测试 4: LLM 对话
    await test_llm_conversation(runtime)

    # 关闭
    await runtime.bus.stop()

    print('\n' + '=' * 70)
    print('  ✓ 核心能力测试完成')
    print('=' * 70)


if __name__ == '__main__':
    asyncio.run(main())
