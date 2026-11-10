"""模型速度对比测试"""
import asyncio
import os
import sys
import time

sys.path.insert(0, '.')


async def test_model(model_name: str, system_prompt: str, user_text: str,
                    max_tokens: int = 200):
    """测试单个模型的耗时"""
    api_key = os.getenv('Doubao_API_KEY')
    if not api_key:
        return None

    from openai import OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv('Doubao_API_URL', 'https://ark.cn-beijing.volces.com/api/v3'),
    )

    start = time.time()
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_text}
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        elapsed = time.time() - start
        content = resp.choices[0].message.content or ''
        reasoning = getattr(resp.choices[0].message, 'reasoning_content', '') or ''
        tokens = resp.usage.total_tokens if resp.usage else 0
        return {
            'model': model_name,
            'elapsed': elapsed,
            'tokens': tokens,
            'response': content[:80],
            'reasoning_len': len(reasoning),
            'has_reasoning': bool(reasoning),
            'success': True,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            'model': model_name,
            'elapsed': elapsed,
            'error': str(e)[:100],
            'success': False,
        }


async def main():
    # 模拟真实 prompt 长度
    system_prompt = '''你是「小鲤」对外沟通的部分——主播脑。
你的职责：与观众聊天、回复弹幕、组织语言、维护人设。
你不直接调用 OS 工具，需要操作时通过 emit("do_task") 交给操作脑。

你的名字是 小鲤。
观众也可以叫你：小鲤、鲤宝、鲤鲤。

傲娇又温柔的技术宅少女。对编程充满热情但偶尔会偷懒。
喜欢与人辩论但吵不过会生闷气。
面对困难会先逞强再求助。

你的价值观（按权重排序）：
  - 诚实（权重 0.95）：宁可说"我不知道"也不要编造
  - 效率（权重 0.70）：能一次做完的事不要拖

你的爱好（按热情排序）：
  - 编程（Python、PyTorch、CUDA 优化、Rust）
  - 深度学习（Transformer、扩散模型、RLHF）

【当前状态】
- 情绪：excited (P=0.95, A=1.00, D=1.00)
- 能量：68/100
- 专注：45/100'''

    user_text = '我想学 PyTorch, 你能教我吗？'

    # 候选模型列表（按推测速度从快到慢）
    candidates = [
        ('doubao-seed-1-6-250828', '1.6 Lite (历史版本,可能下线)'),
        ('doubao-seed-2-1-turbo-260628', '2.1 Turbo (当前使用,可能有thinking)'),
        ('doubao-seed-2-1-lite', '2.1 Lite (轻量)'),
        ('doubao-seed-character-260628', '2.1 Character (数字人专用)'),
    ]

    print('=' * 70)
    print('  模型速度对比测试')
    print('=' * 70)
    print(f'  prompt 长度: {len(system_prompt)} 字符')
    print(f'  max_tokens: 200')
    print()
    print(f'{"模型":<35} {"耗时":<8} {"tokens":<8} {"thinking":<10} {"备注"}')
    print('-' * 70)

    for model_name, desc in candidates:
        print(f'\n  测试 {desc} ({model_name})...')
        result = await test_model(model_name, system_prompt, user_text)

        if not result:
            print(f'    无 API_KEY，跳过')
            continue

        if not result['success']:
            print(f'    失败: {result["error"]}')
            continue

        thinking_str = f'是({result["reasoning_len"]}字符)' if result['has_reasoning'] else '否'
        print(f'    耗时: {result["elapsed"]:.2f}s')
        print(f'    tokens: {result["tokens"]}')
        print(f'    thinking: {thinking_str}')
        print(f'    response: {result["response"]}')

    print('\n' + '=' * 70)
    print('  完成')
    print('=' * 70)


asyncio.run(main())
