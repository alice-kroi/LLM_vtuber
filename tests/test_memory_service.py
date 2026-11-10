# -*- coding: utf-8 -*-
"""persona/memory/service.py 记忆内核单元测试（临时库，不访问真实 memory.db）。"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from persona.memory import service as memory_service_mod
from persona.memory.service import MemoryService
from persona.memory.store import MemoryStore


def _make_service(tmp_path, monkeypatch) -> MemoryService:
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("MEMORY_ENABLED", "1")
    return MemoryService()


def _fake_summarizer(summary="测试摘要", facts=None):
    async def fake(user_text, assistant_text, purpose="general"):
        return {
            "summary": summary,
            "entities": [],
            "importance": 0.6,
            "candidate_facts": list(facts or []),
        }
    return fake


def _add_turn(store, **overrides):
    payload = dict(
        session_id="webui:u1",
        turn_id=uuid.uuid4().hex,
        source="webui",
        visibility="private",
        user_id="u1",
        project_key="llm_vtuber",
        user_text="用户问题",
        assistant_text="助手回答",
        summary="用户喜欢无糖可乐",
        importance=0.6,
    )
    payload.update(overrides)
    return store.add_turn(**payload)


# ---------- 捕获 / 摘要 worker ----------

def test_capture_turn_summarizes_and_creates_proposals(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory_service_mod, "summarize_turn",
        _fake_summarizer("用户告知自己叫小明", [
            {"kind": "profile", "text": "用户叫小明",
             "reason": "自我介绍", "confidence": 0.9},
        ]))

    async def scenario():
        await svc.start()
        await svc.capture_turn(
            "记住：我叫小明，我喜欢无糖可乐",
            "好的，我记住了，以后就叫你小明。",
            source="webui", user="u1")
        await svc.stop()

    asyncio.run(scenario())

    store = MemoryStore(svc.db_path)
    try:
        turns = store.recent_summaries()
        assert len(turns) == 1
        turn = turns[0]
        assert turn["summary"] == "用户告知自己叫小明"
        assert turn["visibility"] == "private"
        assert turn["session_id"] == "webui:u1"
        assert turn["user_id"] == "u1"

        proposals = store.list_proposals()
        assert len(proposals) == 1
        assert proposals[0]["kind"] == "profile"
        assert proposals[0]["text"] == "用户叫小明"

        actions = [row["action"] for row in store.recent_audit(20)]
        assert "turn_captured" in actions
        assert "turn_summarized" in actions
        assert "proposal_created" in actions
    finally:
        store.close()


def test_capture_turn_skips_short_turns(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        await svc.capture_turn("嗯", "好", source="webui", user="u1")
        await svc.stop()

    asyncio.run(scenario())
    store = MemoryStore(svc.db_path)
    try:
        assert store.recent_summaries() == []
    finally:
        store.close()


def test_capture_turn_group_visibility_from_chat_key(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        await svc.capture_turn(
            "群里有人问无糖可乐好不好喝",
            "含糖的少喝一点比较好。",
            source="qq", user="qq:123", chat_key="qq:group:888")
        await svc.stop()

    asyncio.run(scenario())
    store = MemoryStore(svc.db_path)
    try:
        turn = store.recent_summaries()[0]
        assert turn["visibility"] == "group"
        assert turn["session_id"] == "qq:group:888"
    finally:
        store.close()


def test_process_job_filters_meta_rules(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        memory_service_mod, "summarize_turn",
        _fake_summarizer("助教说明记忆工具用法", [
            {"kind": "rule", "text": "可使用memory_status查看记忆内核的状态",
             "reason": "工具用法", "confidence": 0.8},
        ]))

    async def scenario():
        await svc.start()
        store = svc._ensure_store()
        summary_id = _add_turn(store)
        await svc._process_job({
            "summary_id": summary_id,
            "turn_id": "t-meta",
            "session_id": "webui:u1",
            "user_id": "u1",
            "project_key": "llm_vtuber",
            "user_text": "帮我查一下记忆状态",
            "assistant_text": "已经查好了",
        })
        assert store.list_proposals() == []
        await svc.stop()

    asyncio.run(scenario())


# ---------- 建议确认 ----------

def test_add_edit_accept_reject_and_scope(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()

        added = await svc.add("profile", "用户叫小明", user="u1")
        assert added["success"] is True
        assert added["status"] == "pending"

        decided = await svc.decide(
            added["proposal_id"], "edit", text="用户叫小明，喜欢无糖可乐")
        assert decided["success"] is True
        assert decided["status"] == "accepted"

        item = await svc.read(decided["item_id"])
        assert item["success"] is True
        assert item["kind"] == "item"
        assert item["item"]["text"] == "用户叫小明，喜欢无糖可乐"
        assert item["item"]["scope"] == "user"
        assert item["item"]["scope_key"] == "u1"
        assert item["item"]["status"] == "active"

        rule = await svc.add("rule", "不要在群里提真名")
        rejected = await svc.decide(rule["proposal_id"], "reject")
        assert rejected["status"] == "rejected"
        assert (await svc.proposals())["count"] == 0

        invalid = await svc.add("unknown", "非法轨道")
        assert invalid["success"] is False
        empty = await svc.add("profile", "   ")
        assert empty["success"] is False

        await svc.stop()

    asyncio.run(scenario())


def test_decide_missing_proposal_or_bad_action(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        assert (await svc.decide("missing", "accept"))["success"] is False
        added = await svc.add("profile", "用户叫小明")
        assert (await svc.decide(
            added["proposal_id"], "explode"))["success"] is False
        assert (await svc.decide(
            added["proposal_id"], "edit", text="   "))["success"] is False
        await svc.stop()

    asyncio.run(scenario())


# ---------- 召回 / 注入 ----------

def test_build_context_privacy_and_sections(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        store = svc._ensure_store()
        store.add_item("profile", "用户叫小明", scope="user", scope_key="u1")
        store.add_item("profile", "另一个用户是小红",
                       scope="user", scope_key="u2")
        store.add_item("global_fact", "项目使用 Python", scope="global")
        store.add_item("project_key", "当前项目是 LLM_vtuber",
                       scope="project", scope_key="llm_vtuber")
        store.add_item("rule", "不要在群里提真名", scope="global")

        _add_turn(store, session_id="p1", user_id="u1",
                  summary="u1 说喜欢无糖可乐")
        _add_turn(store, session_id="p2", user_id="u2",
                  summary="u2 的私密内容")
        _add_turn(store, session_id="g1", visibility="group",
                  user_id="u1", summary="群里聊到无糖可乐")
        _add_turn(store, session_id="b1", visibility="public",
                  user_id="", summary="公开提到无糖可乐")

        private_ctx = await svc.build_context(
            "无糖可乐", source="webui", user="u1")
        assert "【长期记忆】" in private_ctx
        assert "【记忆使用规则】" in private_ctx
        assert "用户叫小明" in private_ctx
        assert "项目使用 Python" in private_ctx
        assert "当前项目是 LLM_vtuber" in private_ctx
        assert "不要在群里提真名" in private_ctx
        assert "u1 说喜欢无糖可乐" in private_ctx
        assert "群里聊到无糖可乐" in private_ctx
        assert "公开提到无糖可乐" in private_ctx
        assert "另一个用户是小红" not in private_ctx
        assert "u2 的私密内容" not in private_ctx

        group_ctx = await svc.build_context(
            "无糖可乐", source="qq", user="qq:123",
            chat_key="qq:group:888")
        assert "【用户档案】" not in group_ctx
        assert "用户叫小明" not in group_ctx
        assert "群里聊到无糖可乐" in group_ctx
        assert "公开提到无糖可乐" in group_ctx
        assert "u1 说喜欢无糖可乐" not in group_ctx

        public_ctx = await svc.build_context(
            "无糖可乐", source="bilibili", user="")
        assert "【用户档案】" not in public_ctx
        assert "公开提到无糖可乐" in public_ctx
        assert "群里聊到无糖可乐" not in public_ctx
        assert "u1 说喜欢无糖可乐" not in public_ctx

        await svc.stop()

    asyncio.run(scenario())


def test_build_context_empty_store_returns_empty(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        assert await svc.build_context("随便问问", source="webui",
                                       user="u1") == ""
        await svc.stop()

    asyncio.run(scenario())


def test_search_and_read(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        store = svc._ensure_store()
        item_id = store.add_item(
            "profile", "用户养了一只叫煤球的猫",
            scope="user", scope_key="u1")
        summary_id = _add_turn(
            store, summary="用户问煤球几岁了，回答三岁")

        found = await svc.search("煤球")
        assert found["success"] is True
        kinds = {row["kind"] for row in found["results"]}
        assert "profile" in kinds
        assert "turn_summary" in kinds

        item = await svc.read(item_id)
        assert item["item"]["text"] == "用户养了一只叫煤球的猫"
        turn = await svc.read(summary_id)
        assert turn["kind"] == "turn_summary"
        assert turn["item"]["summary"].startswith("用户问煤球")

        missing = await svc.read("nope")
        assert missing["success"] is False

        assert (await svc.recent_items())["count"] == 1
        assert (await svc.recent_turns())["count"] == 1
        await svc.stop()

    asyncio.run(scenario())


def test_status_reports_counts(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    async def scenario():
        await svc.start()
        store = svc._ensure_store()
        store.add_item("profile", "用户叫小明")
        _add_turn(store)
        store.add_proposal("rule", "不要在群里提真名")
        status = await svc.status()
        assert status["success"] is True
        assert status["enabled"] is True
        assert status["turn_summaries"] == 1
        assert status["project_key"] == "llm_vtuber"
        assert status["proposals"][0]["status"] == "pending"
        await svc.stop()

    asyncio.run(scenario())


# ---------- 能力域工具契约 ----------

def test_memory_domain_tool_contract():
    from LLM_v2.domains.memory.memory import MemoryDomain
    from LLM_v2.domains.memory.memory_tools import MEMORY_TOOLS_SCHEMA

    domain = MemoryDomain()
    names = set(domain.allowed_names())
    assert names == {
        "memory_status", "memory_search", "memory_read",
        "memory_add", "memory_proposals", "memory_decide",
    }
    assert set(domain.tool_timeouts) == names
    schema_names = {s["function"]["name"] for s in domain.tools()}
    assert schema_names == names
    assert {s["function"]["name"] for s in MEMORY_TOOLS_SCHEMA} == names
    assert domain.prefix == "memory_"
    assert domain.default_enabled is True
