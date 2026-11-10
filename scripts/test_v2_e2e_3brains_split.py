"""Phase 6 最终验证：3 脑严格分工（用 UTF-8 发送避免 PowerShell 编码问题）"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "http://127.0.0.1:8081"


def post(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}", data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 60)
    print("Phase 6: 3 脑分工 E2E 验证")
    print("=" * 60)

    # 1) GET /api/domains - 域状态
    code, domains = get("/api/domains")
    print(f"\n[1] GET /api/domains: {code}, 域数={len(domains.get('worlds', []))}")

    # 2) GET /api/logs
    code, logs = get("/api/logs?limit=100")
    print(f"[2] GET /api/logs: {code}, 日志数={len(logs.get('logs', []))}")

    # 3) POST /api/input - 让 3 脑全部触发
    print(f"\n[3] POST /api/input: text='帮我打开 Chrome 浏览器'")
    code, body = post("/api/input", {
        "text": "帮我打开 Chrome 浏览器",
        "user": "host_test",
    })
    print(f"    status={code}, body={body}")
    assert code == 200 and body.get("success")

    # 等 3 脑全部处理
    time.sleep(12)

    # 4) 查 /api/brains - 验证 3 脑分工
    code, brains = get("/api/brains")
    print(f"\n[4] GET /api/brains:")
    print(f"    Streamer last_response.text: {(brains['streamer']['last_response']['text'])[:80]}")
    print(f"    WillEngine decision_count: {brains['will_engine']['decision_count']}")

    # 5) 查 /api/events - 验证事件流（USER_MESSAGE / TTS_SPEAK / WILL_ENGINE_DECIDED 等）
    code, events = get("/api/events?limit=100")
    event_types = [e["event_type"] for e in events["events"]]
    type_counts = {t: event_types.count(t) for t in set(event_types)}
    print(f"\n[5] GET /api/events: 事件类型分布")
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    # 6) 查 /api/logs - 看 WillEngine + Operator 日志
    code, logs = get("/api/logs?limit=50")
    print(f"\n[6] GET /api/logs: 来源分布")
    sources = [l["source"] for l in logs["logs"]]
    source_counts = {s: sources.count(s) for s in set(sources)}
    for s, c in sorted(source_counts.items()):
        print(f"    {s}: {c}")

    # 7) 验证 will_engine 真的意图分类了（应有调度日志）
    we_logs = [l for l in logs["logs"] if l["source"] == "will_engine"]
    if any("调度" in l["message"] or "命中" in l["message"] for l in we_logs):
        print(f"\n[7] ✓ WillEngine 调度逻辑触发（有调度/命中日志）")
    else:
        print(f"\n[7] ⚠ WillEngine 未调度（仅日志：{[l['message'][:50] for l in we_logs[:3]]}）")

    # 8) 验证 operator 是否真的执行了工具
    op_logs = [l for l in logs["logs"] if l["source"] == "operator"]
    if op_logs:
        print(f"[8] ✓ Operator 执行: {[l['message'][:60] for l in op_logs[:3]]}")

    print("\n" + "=" * 60)
    print("✅ E2E 验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()