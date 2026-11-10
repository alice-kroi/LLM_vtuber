# -*- coding: utf-8 -*-
"""记忆模块集成测试：服务 ↔ 摘要 ↔ SQLite ↔ WebUI API ↔ 能力域工具。

与单元测试的区别：
- 走真实 summarize_turn 解析链路（仅替换 LLM 客户端，不访问网络）
- 走真实 FastAPI 路由与 Pydantic 请求模型
- 跨两个 MemoryService 实例验证重启持久化
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from persona.memory import service as memory_service_mod
from persona.memory.service import MemoryService


# ---------- 测试替身 ----------

class _FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": json.dumps(self.payload, ensure_ascii=False)}


class _FakeBus:
    def __init__(self):
        self.events = []

    def on(self, *args, **kwargs):
        return None

    async def emit(self, event_type, payload=None):
        self.events.append((event_type, payload))

    def stats(self):
        return {"emitted": len(self.events), "dispatched": 0, "errors": 0}


class _FakeRuntime:
    def __init__(self):
        self.bus = _FakeBus()
        self.persona = SimpleNamespace(name="测试人格")
        self.state = SimpleNamespace()
        self.self_core = None


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("MEMORY_ENABLED", "1")


def _fake_llm(monkeypatch, summary="用户告知自己叫小明，喜欢无糖可乐",
              facts=None):
    fake = _FakeLLM({
        "summary": summary,
        "entities": ["小明"],
        "importance": 0.8,
        "candidate_facts": list(facts or [{
            "kind": "profile",
            "text": "用户叫小明",
            "reason": "自我介绍",
            "confidence": 0.9,
        }]),
    })
    monkeypatch.setattr(
        "LLM_v2.core.llm_adapter.get_llm_for",
        lambda purpose="general", **kwargs: fake)
    return fake


# ---------- 1. 捕获 → 摘要 → 建议 → 召回 → 重启持久化 ----------

def test_kernel_capture_recall_and_restart(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    fake = _fake_llm(monkeypatch)

    svc = MemoryService()

    async def first_run():
        await svc.start()
        await svc.capture_turn(
            "记住：我叫小明，我平时只喝无糖可乐",
            "好的，以后就叫你小明，也只给你推荐无糖的。",
            source="webui", user="u1")
        await svc.stop()

    asyncio.run(first_run())
    assert fake.calls and fake.calls[0]["remember"] is False

    # 第二轮：全新实例读同一个库，模拟进程重启
    svc2 = MemoryService()

    async def second_run():
        await svc2.start()
        status = await svc2.status()
        assert status["turn_summaries"] == 1
        assert status["proposals"][0]["status"] == "pending"

        # 重启后仍可召回上一轮的摘要（建议尚未确认）
        ctx = await svc2.build_context(
            "我叫什么名字？喜欢喝什么？", source="webui", user="u1")
        assert "【相关回忆】" in ctx
        assert "小明" in ctx

        proposals = await svc2.proposals()
        assert proposals["count"] == 1
        proposal = proposals["proposals"][0]
        assert proposal["kind"] == "profile"

        decided = await svc2.decide(proposal["id"], "accept")
        assert decided["success"] is True

        # 确认后长期档案进入注入块
        ctx2 = await svc2.build_context(
            "我叫什么名字？", source="webui", user="u1")
        assert "【用户档案】" in ctx2
        assert "用户叫小明" in ctx2

        found = await svc2.search("小明")
        kinds = {row["kind"] for row in found["results"]}
        assert "profile" in kinds
        assert "turn_summary" in kinds

        actions = [row["action"] for row in (await svc2.status())["recent_audit"]]
        assert "proposal_accepted" in actions
        await svc2.stop()

    asyncio.run(second_run())


# ---------- 2. WebUI HTTP API 全链路 ----------

@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    svc = MemoryService()
    monkeypatch.setattr(memory_service_mod, "_SERVICE", svc)

    from LLM_v2.io.webui import create_webui_app
    app = create_webui_app(_FakeRuntime())
    with TestClient(app) as client:
        yield client

    if svc._store is not None:
        svc._store.close()
        svc._store = None


def test_webui_memory_api_full_flow(api_client):
    client = api_client

    r = client.get("/api/memory/kernel")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["status"]["turn_summaries"] == 0
    assert body["proposals"] == []

    # 手动添加 → 待确认
    r = client.post("/api/memory/kernel/add", json={
        "track": "profile",
        "text": "我养了一只叫煤球的猫",
        "reason": "WebUI 集成测试",
    })
    assert r.status_code == 200
    added = r.json()
    assert added["success"] is True
    assert added["status"] == "pending"

    r = client.get("/api/memory/kernel")
    pending = r.json()["proposals"]
    assert len(pending) == 1
    proposal_id = pending[0]["id"]

    # 编辑后采纳
    r = client.post("/api/memory/kernel/decide", json={
        "id": proposal_id,
        "action": "edit",
        "text": "我养了一只叫煤球的猫，今年三岁",
    })
    assert r.status_code == 200
    decided = r.json()
    assert decided["success"] is True
    assert decided["status"] == "accepted"
    item_id = decided["item_id"]

    r = client.get("/api/memory/kernel")
    body = r.json()
    assert body["proposals"] == []
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["text"] == "我养了一只叫煤球的猫，今年三岁"
    # WebUI 手动添加的作用域与聊天栏一致，保证能被召回
    assert item["scope"] == "user"
    assert item["scope_key"] == "web_console"

    # 检索 + 读取
    r = client.get("/api/memory/kernel/search",
                   params={"q": "煤球", "limit": 10})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results and any("煤球" in row["text"] for row in results)

    r = client.get(f"/api/memory/kernel/read/{item_id}")
    assert r.status_code == 200
    assert r.json()["item"]["text"] == "我养了一只叫煤球的猫，今年三岁"

    # 拒绝流程
    r = client.post("/api/memory/kernel/add", json={
        "track": "global_fact", "text": "我的生日是 3 月 14 日"})
    rule_id = r.json()["proposal_id"]
    r = client.post("/api/memory/kernel/decide", json={
        "id": rule_id, "action": "reject"})
    assert r.json()["status"] == "rejected"
    assert client.get("/api/memory/kernel").json()["proposals"] == []

    # 错误分支保持 200 + success=false，前端可直接提示
    r = client.post("/api/memory/kernel/decide", json={
        "id": "missing-id", "action": "accept"})
    assert r.status_code == 200
    assert r.json()["success"] is False

    r = client.post("/api/memory/kernel/add", json={
        "track": "bad-track", "text": "x"})
    assert r.status_code == 200
    assert r.json()["success"] is False


# ---------- 3. 能力域工具 ↔ 服务 ----------

def test_memory_domain_tools_use_service(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    svc = MemoryService()
    monkeypatch.setattr(memory_service_mod, "_SERVICE", svc)

    from LLM_v2.domains.memory.memory import MemoryDomain

    domain = MemoryDomain()

    async def scenario():
        status = await domain.memory_status()
        assert status["enabled"] is True

        added = await domain.memory_add("profile", "用户叫小明")
        assert added["success"] is True
        assert added["status"] == "pending"

        proposals = await domain.memory_proposals()
        assert proposals["count"] == 1
        proposal_id = proposals["proposals"][0]["id"]

        decided = await domain.memory_decide(proposal_id, "accept")
        assert decided["success"] is True

        found = await domain.memory_search("小明")
        assert found["count"] >= 1

        read = await domain.memory_read(decided["item_id"])
        assert read["success"] is True
        assert read["item"]["text"] == "用户叫小明"

        await svc.stop()

    asyncio.run(scenario())
