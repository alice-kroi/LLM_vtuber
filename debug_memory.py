#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试 InMemoryStore 保存/加载问题"""

import asyncio
from langgraph.store.memory import InMemoryStore


async def debug():
    store = InMemoryStore()
    user_id = 'user_test_user'

    # 保存消息
    await store.aput(('user_memory', user_id), 'msg_1', {
        'type': 'message', 'role': 'user',
        'content': '你好，我叫测试用户', 'timestamp': 12345.0
    })
    await store.aput(('user_memory', user_id), 'msg_2', {
        'type': 'message', 'role': 'assistant',
        'content': '你好测试用户！', 'timestamp': 12346.0
    })
    print('保存 2 条消息成功')

    # 方法1: aget 直接获取
    print('\n--- 方法1: aget ---')
    item = await store.aget(('user_memory', user_id), 'msg_1')
    print(f'aget msg_1: {item.value if item else None}')

    # 方法2: asearch (带 query)
    print('\n--- 方法2: asearch (query="") ---')
    items = await store.asearch(('user_memory', user_id), query='', limit=50)
    print(f'结果数量: {len(items)}')
    for i in items:
        print(f'  key={i.key}, value={i.value}')

    # 方法3: asearch (不带 query)
    print('\n--- 方法3: asearch (无 query 参数) ---')
    items = await store.asearch(('user_memory', user_id), limit=50)
    print(f'结果数量: {len(items)}')
    for i in items:
        print(f'  key={i.key}, value={i.value}')

    # 方法4: 搜索整个 user_memory
    print('\n--- 方法4: asearch (user_memory,) ---')
    items = await store.asearch(('user_memory',), query='', limit=100)
    print(f'结果数量: {len(items)}')
    for i in items:
        print(f'  ns={i.namespace}, key={i.key}')

    # 方法5: list_namespaces
    print('\n--- 方法5: alist_namespaces ---')
    ns_list = await store.alist_namespaces(prefix=('user_memory',))
    print(f'命名空间列表: {ns_list}')

    print('\n调试完成!')


asyncio.run(debug())
