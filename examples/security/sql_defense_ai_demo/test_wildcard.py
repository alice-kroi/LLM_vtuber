import sys
sys.path.insert(0, '.')
from app import app
import json

client = app.test_client()

print("Testing wildcard injection fix...\n")

# Test 1: q=% should not return all users
print("1. Testing /users/search?q=%...")
response = client.get('/users/search?q=%')
print(f"Status: {response.status_code}")
data = json.loads(response.data.decode('utf-8'))
print(f"Results count: {len(data)}")
if len(data) == 0:
    print("✓ Passed: No users returned for % wildcard")
else:
    print(f"✗ Failed: {len(data)} users returned")

# Test 2: q=Al should still return Alice
print("\n2. Testing /users/search?q=Al...")
response = client.get('/users/search?q=Al')
print(f"Status: {response.status_code}")
data = json.loads(response.data.decode('utf-8'))
print(f"Results count: {len(data)}")
if len(data) == 1 and data[0]['name'] == 'Alice':
    print("✓ Passed: Alice found correctly")
else:
    print(f"✗ Failed: Expected 1 result, got {len(data)}")

# Test 3: q=Bob should still work
print("\n3. Testing /users/search?q=Bob...")
response = client.get('/users/search?q=Bob')
print(f"Status: {response.status_code}")
data = json.loads(response.data.decode('utf-8'))
print(f"Results count: {len(data)}")
if len(data) == 1 and data[0]['name'] == 'Bob':
    print("✓ Passed: Bob found correctly")
else:
    print(f"✗ Failed: Expected 1 result, got {len(data)}")
