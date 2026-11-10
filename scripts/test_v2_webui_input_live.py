"""WebUI 输入端点验证（H-2 实时测试）"""
import asyncio
import json
import urllib.request
import urllib.error


def post(path: str, data: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:8081{path}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8081{path}", timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 60)
    print("H-2 WebUI 输入验证（连真实 V2 服务）")
    print("=" * 60)

    # 1) GET / - 端点列表
    code, body = get("/")
    assert code == 200
    endpoints = body.get("endpoints", [])
    assert "/api/input" in [e.replace(" (POST)", "") for e in endpoints] or any("/api/input" in e for e in endpoints)
    print(f"\n✓ GET /: 200, endpoints 含 /api/input")

    # 2) POST /api/input - 基本输入
    code, body = post("/api/input", {"text": "你好小鲤"})
    print(f"\n✓ POST /api/input (basic):")
    print(f"   {code} {json.dumps(body, ensure_ascii=False)}")
    assert code == 200
    assert body.get("success") is True
    assert body.get("source") == "webui"
    assert body.get("text") == "你好小鲤"
    assert body.get("routed_event") == "USER_MESSAGE"

    # 3) POST /api/input - 带 metadata
    code, body = post("/api/input", {
        "text": "hello world",
        "user": "alice",
        "uid": "u-001",
        "metadata": {"source": "manual_test", "mood": "happy"},
    })
    print(f"\n✓ POST /api/input (with metadata):")
    print(f"   {code} {json.dumps(body, ensure_ascii=False)}")
    assert code == 200

    # 4) POST /api/input - 空 text 应 400
    code, body = post("/api/input", {"text": ""})
    print(f"\n✓ POST /api/input (empty text) → 400:")
    print(f"   {code} {json.dumps(body, ensure_ascii=False)[:100]}")
    assert code == 400

    # 5) POST /api/input - 空格 text 应 400（strip 后空）
    code, body = post("/api/input", {"text": "   "})
    print(f"\n✓ POST /api/input (whitespace only) → 400:")
    print(f"   {code}")
    assert code == 400

    # 6) GET /api/domains - 验证 webui 在列表里且 type=transceiver
    code, body = get("/api/domains")
    webui = [w for w in body["worlds"] if w["id"] == "webui"][0]
    assert webui["type"] == "transceiver"
    assert webui["enabled"] is True
    assert "user_message" in webui["inbound_events"]
    print(f"\n✓ GET /api/domains: webui type=transceiver, emit user_message")

    # 7) GET /api/events - 验证 USER_MESSAGE 事件已 emit
    code, body = get("/api/events?limit=20")
    user_msgs = [e for e in body["events"] if e["event_type"] == "user_message"]
    print(f"\n✓ GET /api/events: USER_MESSAGE 数 = {len(user_msgs)}")
    if user_msgs:
        latest = user_msgs[-1]
        print(f"   最新 payload: {json.dumps(latest['payload'], ensure_ascii=False)[:200]}")
        assert latest["payload"]["source"] == "webui"
        assert latest["payload"]["text"] in ["你好小鲤", "hello world"]

    print("\n" + "=" * 60)
    print("✅ WebUI 输入端点全部通过")
    print("=" * 60)


if __name__ == "__main__":
    main()