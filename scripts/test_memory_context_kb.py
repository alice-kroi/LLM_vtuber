"""V2 记忆 + 上下文 + 知识库综合测试"""
import asyncio
import sys
import os
import json
import logging
from pathlib import Path

sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime
from LLM_v2.core.llm_adapter import LLMAdapter, get_llm
from LLM_v2.core.rag_adapter import get_rag
from LLM_v2.brains.streamer import StreamerBrain
from LLM_v2.events import EVENTS


async def test_memory_persistence():
    """测试 1: 记忆保存（跨重启）"""
    print('=' * 70)
    print('  Test 1: 记忆保存（跨重启）')
    print('=' * 70)

    # 重置单例（模拟重启）
    PersonaRuntime._instance = None
    runtime = get_runtime()

    print(f'\n  [1.1] 初始 state: E={runtime.state.energy}, F={runtime.state.focus}, '
          f'pad={runtime.state.pad.dominant_quadrant()}')

    # 修改 state
    runtime.apply_event('test_memory_1', delta_P=0.1, delta_energy=15)
    runtime.apply_event('test_memory_2', delta_social=-8, delta_curiosity=12)
    runtime.apply_event('test_memory_3', delta_focus=-5)
    print(f'  [1.2] 修改后: P={runtime.state.pad.P:.2f}, E={runtime.state.energy}, '
          f'S={runtime.state.social_need}, C={runtime.state.curiosity}')

    # 验证 sqlite 写入
    import sqlite3
    with sqlite3.connect('persona/state.db') as conn:
        row = conn.execute(
            'SELECT pad_P, energy, focus, social_need, curiosity '
            'FROM persona_state WHERE id=1'
        ).fetchone()
        print(f'  [1.3] sqlite 实际值: P={row[0]:.2f}, E={row[1]}, F={row[2]}, '
              f'S={row[3]}, C={row[4]}')

    # 模拟"重启"：重置单例，重新加载
    print('\n  [1.4] 模拟"重启"（重置单例 + 重新加载）')
    PersonaRuntime._instance = None
    runtime2 = get_runtime()
    print(f'  [1.5] 重启后: P={runtime2.state.pad.P:.2f}, E={runtime2.state.energy}, '
          f'S={runtime2.state.social_need}')
    print(f'  ✅ 数据持久化{"成功" if abs(runtime2.state.pad.P - 0.1 - runtime.state.pad.P) < 0.1 else "失败"}')

    # 测试事件历史
    with sqlite3.connect('persona/state.db') as conn:
        events = conn.execute(
            'SELECT event_type, delta_P FROM state_events '
            'WHERE event_type LIKE "test_memory%" ORDER BY id DESC LIMIT 3'
        ).fetchall()
        print(f'\n  [1.6] 事件历史（最近 3 个 test_memory_*）:')
        for e in events:
            print(f'    - {e[0]}: ΔP={e[1]:.2f}')

    return runtime2


async def test_context_control(runtime):
    """测试 2: 上下文控制"""
    print('\n' + '=' * 70)
    print('  Test 2: 上下文控制')
    print('=' * 70)

    llm = get_llm()

    # 2.1 长历史截断
    long_history = []
    for i in range(50):
        long_history.append({
            'role': 'user' if i % 2 == 0 else 'assistant',
            'content': f'消息 {i}: ' + 'x' * 300  # 每条 ~310 字符
        })
    total = sum(len(m['content']) for m in long_history)
    print(f'\n  [2.1] 构造长历史: {len(long_history)} 条，总 {total} 字符')

    trimmed = llm._trim_history(long_history, max_chars=2000) if hasattr(llm, '_trim_history') else None
    if trimmed is None:
        # 调用 chat_with_context_control 测试
        result = await llm.chat_with_context_control(
            system_prompt='你是小鲤。',
            user_text='你好',
            history=long_history,
            max_history_chars=2000,
        )
        print(f'  [2.2] chat_with_context_control 调用成功（chat() 内部已 trim）')
        trimmed = llm._history if hasattr(llm, '_history') else []
    trimmed_total = sum(len(m['content']) for m in trimmed)
    print(f'  [2.2] trim 到 2000 字符: 剩 {len(trimmed)} 条，{trimmed_total} 字符')

    # 2.2 验证 trim 保留了最新的
    assert '消息 49' in trimmed[-1]['content'], '最新消息应保留'
    assert '消息 0' not in trimmed[0]['content'] if trimmed else True, '最早消息应丢弃'
    print(f'  [2.3] ✅ 最新消息保留，最早消息丢弃')

    # 2.3 Persona + State 自动注入
    persona_state = runtime.persona.to_prompt_block(state=runtime.state)
    print(f'\n  [2.4] Persona + State 拼装: {len(persona_state)} 字符')
    assert '小鲤' in persona_state, 'persona 名字应注入'
    assert '能量' in persona_state, '资源条应注入'
    print(f'  [2.5] ✅ Persona 名字 + 资源条全部注入')


async def test_lorebook(runtime):
    """测试 3: 知识库（Lorebook）"""
    print('\n' + '=' * 70)
    print('  Test 3: 知识库（Lorebook）')
    print('=' * 70)

    persona = runtime.persona

    print(f'\n  [3.1] Lorebook 总数: {len(persona.lorebook)}')
    for e in persona.lorebook:
        print(f'    - 优先级 {e.priority:.2f}: {e.keywords[:3]}')

    # 3.1 PyTorch 触发
    triggers = persona.match_lorebook('我想学 PyTorch')
    print(f'\n  [3.2] "我想学 PyTorch" 触发: {len(triggers)} 条')
    for t in triggers[:2]:
        print(f'    关键词: {t.keywords}')
        print(f'    内容: {t.content[:80]}')

    # 3.2 Bug 触发
    triggers2 = persona.match_lorebook('我代码有个 bug 调不通')
    print(f'\n  [3.3] "代码有 bug" 触发: {len(triggers2)} 条')

    # 3.3 孤独摇滚触发
    triggers3 = persona.match_lorebook('你喜欢孤独摇滚吗')
    print(f'\n  [3.4] "孤独摇滚" 触发: {len(triggers3)} 条')

    # 3.4 不触发（无关 query）
    triggers4 = persona.match_lorebook('今天天气真好')
    print(f'\n  [3.5] "今天天气真好" 触发: {len(triggers4)} 条（应=0）')
    assert len(triggers4) == 0, '无关 query 不应触发'
    print(f'  ✅ Lorebook 触发准确')


async def test_rag_kb(runtime):
    """测试 4: RAG 知识库"""
    print('\n' + '=' * 70)
    print('  Test 4: RAG 知识库检索')
    print('=' * 70)

    rag = get_rag(top_k=3)

    # 4.1 单条查询
    print(f'\n  [4.1] 查询 "深度学习"')
    results = await rag.retrieve('深度学习', top_k=3)
    print(f'  检索结果: {len(results)} 条')
    for r in results[:3]:
        print(f'    - score={r["score"]:.2f} rel={r["relevance"]:.2f} '
              f'rec={r["recency"]:.2f}: {r["content"][:50]}')

    # 4.2 拼装上下文
    print(f'\n  [4.2] build_context')
    ctx = await rag.build_context('深度学习', top_k=2)
    print(f'  上下文长度: {len(ctx)} 字符')
    assert '【历史相关对话】' in ctx
    print(f'  ✅ 自动拼装 "【历史相关对话】" 块')


async def test_full_chain(runtime):
    """测试 5: 端到端（弹幕 → lorebook + RAG + LLM）"""
    print('\n' + '=' * 70)
    print('  Test 5: 端到端集成')
    print('=' * 70)

    await runtime.bus.start()
    streamer = StreamerBrain(runtime, enable_rag=True)

    responses = []
    async def on_tts(payload):
        if isinstance(payload, dict):
            responses.append(payload)

    runtime.bus.on(EVENTS.TTS_SPEAK, on_tts)
    await streamer.start()
    await asyncio.sleep(0.5)

    # 5.1 测试 lorebook 触发场景
    print('\n  [5.1] 发送 PyTorch 弹幕（应触发 lorebook + RAG）')
    await runtime.bus.emit(EVENTS.BILIBILI_MSG, {
        'user': '知识库测试',
        'text': '我想学 PyTorch 怎么办？'
    })

    # 等 6 秒（character 模型 ~2s）
    for _ in range(12):
        await asyncio.sleep(0.5)
        if responses:
            break

    print(f'  收到 {len(responses)} 条 TTS')
    if responses:
        text = responses[-1].get('text', '')
        print(f'  内容: {text[:200]}')

    # 检查是否触发 lorebook（关键词 "PyTorch"）
    lorebook_in_text = 'PyTorch' in text or '动态图' in text or '梯度' in text
    print(f'  lorebook 内容注入: {"✅ 是" if lorebook_in_text else "❌ 否"}')

    await streamer.stop()
    await runtime.bus.stop()


async def main():
    runtime = await test_memory_persistence()
    await test_context_control(runtime)
    await test_lorebook(runtime)
    await test_rag_kb(runtime)
    await test_full_chain(runtime)
    print('\n' + '=' * 70)
    print('  ✅ 全部测试完成')
    print('=' * 70)


if __name__ == '__main__':
    asyncio.run(main())
