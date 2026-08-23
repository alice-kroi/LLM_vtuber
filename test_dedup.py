import requests, time

# 发送3条相同的测试消息
for i in range(3):
    r = requests.post('http://localhost:8081/api/send', json={'content': '去重测试', 'name': '测试用户'})
    print(f"发送 {i+1}: {r.json()}")

time.sleep(3)

# 检查消息历史
r = requests.get('http://localhost:8081/api/messages')
d = r.json()
print(f"\n总消息数: {d['total']}")

# 筛选"去重测试"消息
msgs = [m for m in d['messages'] if m['data'].get('content') == '去重测试']
print(f"去重测试消息数: {len(msgs)}")
for m in msgs:
    print(f"  [{m['type']}] ts={m['timestamp']:.3f}")