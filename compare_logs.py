#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 bili_main 日志和主服务日志"""

import os
import requests

# 1. 检查 bili_main 日志中的弹幕
print('📋 bili_main 日志中的弹幕:')
print('=' * 60)

log_path = os.path.join('broadcast', 'bili_main.log')
with open(log_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

danmaku = [l for l in lines if '[弹幕]' in l]
print(f'总弹幕数: {len(danmaku)}')

for line in danmaku[-10:]:
    print(f'  {line.strip()[:150]}')

# 2. 检查主服务接收到的消息
print(f'\n📥 主服务接收到的消息:')
print('=' * 60)

logs = requests.get('http://localhost:8081/api/logs?limit=500', timeout=5).json().get('logs', [])
init_logs = [l for l in logs if '初始状态' in l.get('message', '')]

import re
for log in init_logs:
    msg = log['message']
    if '"role": "user"' in msg:
        match = re.search(r'"content":\s*"([^"]+)"', msg)
        if match:
            content = match.group(1)[:100]
            print(f'  - {content}')

# 3. 检查消息队列状态
print(f'\n📊 服务状态:')
print('=' * 60)
status = requests.get('http://localhost:8081/api/status', timeout=5).json()
print(f'  已处理消息: {status.get("messages_processed", 0)}')
print(f'  队列大小: {status.get("queue", {}).get("size", 0)}')
print(f'  运行时间: {status.get("uptime", 0)}秒')
