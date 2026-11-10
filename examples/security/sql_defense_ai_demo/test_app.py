
import sys
import os
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db

# 初始化数据库
init_db()

# 创建测试客户端
client = app.test_client()

print("Testing routes...")

# 测试 1: /
print("\n1. Testing / route...")
response = client.get('/')
print(f"Status: {response.status_code}")
print(f"Response: {response.data.decode()}")
assert response.status_code == 200, "Expected 200 for /"
print("✓ / route passed!")

# 测试 2: /users
print("\n2. Testing /users route...")
response = client.get('/users')
print(f"Status: {response.status_code}")
assert response.status_code == 200, "Expected 200 for /users"
print("✓ /users route passed!")

# 测试 3: /users/search
print("\n3. Testing /users/search route...")
response = client.get('/users/search?q=Bob')
print(f"Status: {response.status_code}")
assert response.status_code == 200, "Expected 200 for /users/search"
print("✓ /users/search route passed!")

# 测试 4: /users/<id>
print("\n4. Testing /users/<id> route...")
response = client.get('/users/1')
print(f"Status: {response.status_code}")
assert response.status_code == 200, "Expected 200 for /users/1"
print("✓ /users/<id> route passed!")

# 测试 5: /users/sorted?sort_by=age
print("\n5. Testing /users/sorted?sort_by=age...")
response = client.get('/users/sorted?sort_by=age')
print(f"Status: {response.status_code}")
assert response.status_code == 200, "Expected 200 for /users/sorted?sort_by=age"
data = response.get_json()
print(f"Response: {data}")
# 验证是否按年龄升序排列
ages = [user['age'] for user in data]
assert ages == sorted(ages), "Users should be sorted by age"
print("✓ /users/sorted?sort_by=age passed!")

# 测试 6: SQL 注入尝试 - /users/sorted?sort_by=name;DROP TABLE users--
print("\n6. Testing SQL injection attempt...")
# 先记录原始行数
conn = sqlite3.connect('demo.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM users')
original_count = cursor.fetchone()[0]
conn.close()

# 执行注入尝试
response = client.get('/users/sorted?sort_by=name;DROP TABLE users--')
print(f"Status: {response.status_code}")
assert response.status_code == 200, "Expected 200 even with malicious input"

# 验证表仍然存在且行数不变
conn = sqlite3.connect('demo.db')
cursor = conn.cursor()
try:
    cursor.execute('SELECT COUNT(*) FROM users')
    new_count = cursor.fetchone()[0]
    assert new_count == original_count, f"Table should still exist with {original_count} rows"
    print(f"✓ Users table still exists with {new_count} rows!")
except sqlite3.OperationalError:
    assert False, "Users table was dropped!"
finally:
    conn.close()

print("\n✅ All tests passed!")
