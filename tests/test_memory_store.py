# -*- coding: utf-8 -*-
"""persona/memory/store.py SQLite 读写单元测试。"""
from __future__ import annotations

import pytest

from persona.memory.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    db = MemoryStore(tmp_path / "memory.db")
    yield db
    db.close()


def _add_turn(store, *, session_id="s1", visibility="private",
              user_id="u1", summary="用户喜欢无糖可乐"):
    return store.add_turn(
        session_id=session_id,
        turn_id="t-" + session_id,
        source="webui",
        visibility=visibility,
        user_id=user_id,
        project_key="llm_vtuber",
        user_text="用户问题",
        assistant_text="助手回答",
        summary=summary,
        importance=0.6,
    )


# ---------- 长期条目 ----------

def test_add_and_get_item(store):
    item_id = store.add_item(
        "profile", "用户叫小明", scope="user", scope_key="u1",
        summary="叫小明", importance=0.8, confidence=0.9,
        evidence={"turn_id": "t1"})
    item = store.get_item(item_id)
    assert item is not None
    assert item["track"] == "profile"
    assert item["scope"] == "user"
    assert item["scope_key"] == "u1"
    assert item["status"] == "active"
    assert item["text"] == "用户叫小明"
    assert item["evidence_json"] == {"turn_id": "t1"}
    assert item["use_count"] == 0
    assert item["hash"]


def test_list_items_filters_by_track_scope_and_status(store):
    store.add_item("profile", "用户叫小明", scope="user", scope_key="u1")
    store.add_item("global_fact", "Milvus 端口 19530", scope="global")
    store.add_item("rule", "不要在群里提真名", scope="global",
                   status="pending")
    assert len(store.list_items(track="profile", scope="user",
                                scope_key="u1")) == 1
    assert len(store.list_items(track="global_fact", scope="global")) == 1
    assert store.list_items(status="pending")[0]["track"] == "rule"
    assert store.list_items(track="profile", scope="user",
                            scope_key="u2") == []


def test_update_item_usage(store):
    first = store.add_item("profile", "用户叫小明")
    second = store.add_item("global_fact", "项目使用 Python")
    store.update_item_usage([first, second, "missing-id"])
    assert store.get_item(first)["use_count"] == 1
    assert store.get_item(second)["use_count"] == 1
    assert store.get_item(first)["last_used_at"]


# ---------- 轮次摘要 ----------

def test_add_update_and_get_summary(store):
    summary_id = _add_turn(store)
    store.update_turn(summary_id, summary="用户确认喜欢无糖可乐",
                      entities=["无糖可乐", "用户"], importance=0.8)
    row = store.get_summary(summary_id)
    assert row["summary"] == "用户确认喜欢无糖可乐"
    assert row["entities_json"] == ["无糖可乐", "用户"]
    assert row["importance"] == 0.8
    assert row["visibility"] == "private"


def test_recent_summaries_visibility_filter_does_not_leak_other_users(store):
    _add_turn(store, session_id="a", user_id="u1",
              summary="u1 的私聊记忆")
    _add_turn(store, session_id="b", user_id="u2",
              summary="u2 的私聊记忆")
    _add_turn(store, session_id="c", visibility="group", user_id="u1",
              summary="群聊记忆")
    _add_turn(store, session_id="d", visibility="public", user_id="",
              summary="公开记忆")

    rows = store.recent_summaries(visibility="private", user_id="u1")
    texts = [r["summary"] for r in rows]
    assert "u1 的私聊记忆" in texts
    assert "群聊记忆" in texts            # 私聊场景可回忆群聊/公开
    assert "公开记忆" in texts
    assert "u2 的私聊记忆" not in texts   # 不能串到别的用户

    group_rows = store.recent_summaries(visibility="group")
    group_texts = [r["summary"] for r in group_rows]
    assert "群聊记忆" in group_texts
    assert "公开记忆" in group_texts
    assert "u1 的私聊记忆" not in group_texts

    public_rows = store.recent_summaries(visibility="public")
    assert [r["summary"] for r in public_rows] == ["公开记忆"]


def test_recent_summaries_limit_and_order(store):
    for i in range(5):
        _add_turn(store, session_id=f"s{i}", summary=f"摘要{i}")
    rows = store.recent_summaries(limit=3)
    assert len(rows) == 3


# ---------- 建议队列 ----------

def test_add_proposal_exact_dedup_increments_frequency(store):
    first = store.add_proposal("profile", "用户叫小明")
    second = store.add_proposal("profile", "用户叫小明")
    assert first == second
    rows = store.list_proposals()
    assert len(rows) == 1
    assert rows[0]["frequency"] == 2


def test_add_proposal_near_duplicate_merges_and_keeps_longer_text(store):
    store.add_proposal("profile", "用户喜欢喝无糖可乐")
    store.add_proposal("profile", "用户喜欢喝无糖可乐和果汁")
    rows = store.list_proposals()
    assert len(rows) == 1
    assert rows[0]["text"] == "用户喜欢喝无糖可乐和果汁"
    assert rows[0]["frequency"] == 2


def test_add_proposal_different_tracks_do_not_merge(store):
    store.add_proposal("profile", "用户喜欢喝无糖可乐")
    store.add_proposal("global_fact", "用户喜欢喝无糖可乐")
    assert len(store.list_proposals()) == 2


def test_decide_proposal_accept_edit_reject(store):
    accept_id = store.add_proposal("profile", "用户叫小明")
    edit_id = store.add_proposal("rule", "不要在群里提真名")
    reject_id = store.add_proposal("global_fact", "用户住在杭州")

    assert store.decide_proposal(accept_id, "accepted", actor="tester")
    assert store.get_proposal(accept_id)["status"] == "accepted"
    assert store.get_proposal(accept_id)["decided_by"] == "tester"

    assert store.decide_proposal(edit_id, "accepted",
                                 text="不要在公开场合提真名", actor="tester")
    assert store.get_proposal(edit_id)["text"] == "不要在公开场合提真名"

    assert store.decide_proposal(reject_id, "rejected", actor="tester")
    assert store.get_proposal(reject_id)["status"] == "rejected"
    assert store.list_proposals(status="pending") == []
    assert store.decide_proposal("missing", "accepted") is False


# ---------- 审计 / 状态 ----------

def test_audit_and_status(store):
    store.audit("turn_captured", item_id="s1", actor="streamer",
                result="hello", session_id="s1")
    store.audit("proposal_created", item_id="p1", actor="memory")
    rows = store.recent_audit(limit=1)
    assert len(rows) == 1
    assert rows[0]["action"] == "proposal_created"

    store.add_item("profile", "用户叫小明")
    _add_turn(store)
    store.add_proposal("rule", "不要在群里提真名")
    status = store.status()
    assert status["turn_summaries"] == 1
    assert status["audit_rows"] == 2
    assert {row["status"] for row in status["proposals"]} == {"pending"}
    assert {row["track"] for row in status["items"]} == {"profile"}
