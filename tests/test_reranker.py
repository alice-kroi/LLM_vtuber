"""rerank：提示词构造、输出解析、失败回退测试。"""
from __future__ import annotations

import asyncio

from LLM_v2.core.reranker import (
    LLMReranker, LocalReranker, build_reranker, build_prompt, parse_order)

ITEMS = [
    {"content": "综述概览：大模型的能力与挑战", "title": "综述", "page_from": 3, "score": 0.83},
    {"content": "局限与开放问题：原理不透明、训练成本高", "title": "综述", "page_from": 99, "score": 0.81},
    {"content": "应用场景：科研与产业", "title": "综述", "page_from": 70, "score": 0.80},
]


class FakeLLM:
    def __init__(self, reply="", error: Exception | None = None,
                 delay: float = 0.0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.prompts = []

    async def chat_simple(self, system_prompt, user_text, images=None, **kwargs):
        self.prompts.append((system_prompt, user_text))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.reply


def test_build_prompt_contains_refs_and_format():
    prompt = build_prompt("大模型的局限", ITEMS, top_k=2, snippet_chars=100)
    assert "问题：大模型的局限" in prompt
    assert "[综述 p.3]" in prompt and "[综述 p.99]" in prompt
    assert '"order"' in prompt
    assert "最多 2 个" in prompt


def test_parse_order_valid_json():
    assert parse_order('{"order": [2, 1]}', 3) == [2, 1]


def test_parse_order_filters_out_of_range_and_duplicates():
    assert parse_order('{"order": [2, 2, 9, 1]}', 3) == [2, 1]


def test_parse_order_falls_back_to_brackets():
    assert parse_order("我认为应该选 [3, 2]", 3) == [3, 2]


def test_parse_order_handles_garbage():
    assert parse_order("完全不是 JSON", 3) == []


def test_rerank_reorders_by_model_output():
    llm = FakeLLM('{"order": [2, 1]}')

    async def run():
        reranker = LLMReranker(llm=llm, timeout=1.0)
        return await reranker.rerank("大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [99, 3]
    assert all(h.get("reranked") for h in hits)
    assert "大模型的局限" in llm.prompts[0][1]


def test_rerank_fills_to_top_k_when_model_selects_fewer():
    llm = FakeLLM('{"order": [2]}')

    async def run():
        reranker = LLMReranker(llm=llm, timeout=1.0)
        return await reranker.rerank("大模型的局限", [dict(i) for i in ITEMS], top_k=3)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [99, 3, 70]


def test_rerank_keeps_order_when_model_returns_empty_selection():
    llm = FakeLLM('{"order": []}')

    async def run():
        reranker = LLMReranker(llm=llm, timeout=1.0)
        return await reranker.rerank("大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [3, 99]
    assert not hits[0].get("reranked")


def test_rerank_falls_back_on_exception():
    llm = FakeLLM(error=RuntimeError("api down"))

    async def run():
        reranker = LLMReranker(llm=llm, timeout=1.0)
        return await reranker.rerank("问题", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [3, 99]


def test_rerank_falls_back_on_timeout():
    llm = FakeLLM('{"order": [2, 1]}', delay=0.3)

    async def run():
        reranker = LLMReranker(llm=llm, timeout=0.05)
        return await reranker.rerank("问题", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [3, 99]


def test_rerank_caps_candidates_in_prompt():
    many = [dict(ITEMS[0], page_from=i) for i in range(1, 11)]
    llm = FakeLLM('{"order": [1]}')

    async def run():
        reranker = LLMReranker(llm=llm, candidates=4, timeout=1.0)
        return await reranker.rerank("问题", many, top_k=1)

    asyncio.run(run())
    prompt = llm.prompts[0][1]
    assert "[综述 p.4]" in prompt
    assert "[综述 p.5]" not in prompt


# ---------- 本地 cross-encoder ----------

def test_local_rerank_orders_by_score():
    def scorer(query, passages):
        return [0.2, 0.9, 0.5]

    async def run():
        reranker = LocalReranker(model_path="dummy", _scorer=scorer)
        return await reranker.rerank(
            "大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [99, 70]
    assert hits[0]["rerank_score"] == 0.9
    assert all(h.get("reranked") for h in hits)


def test_local_rerank_falls_back_when_model_unavailable():
    async def run():
        reranker = LocalReranker(model_path="dummy", candidates=3)
        reranker._load_failed = True       # 模拟加载失败，避免真的读模型
        return await reranker.rerank(
            "大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [3, 99]
    assert not hits[0].get("reranked")


def test_local_rerank_falls_back_when_scorer_raises():
    def scorer(query, passages):
        raise RuntimeError("cuda oom")

    async def run():
        reranker = LocalReranker(model_path="dummy", _scorer=scorer)
        return await reranker.rerank(
            "大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    assert [h["page_from"] for h in asyncio.run(run())] == [3, 99]


# ---------- backend 工厂 ----------

def test_local_rerank_defers_first_call_until_ready():
    async def run():
        reranker = LocalReranker(model_path="dummy", candidates=3)
        reranker._warming = True   # 阻止真的起后台线程加载模型
        return await reranker.rerank(
            "大模型的局限", [dict(i) for i in ITEMS], top_k=2)

    hits = asyncio.run(run())
    assert [h["page_from"] for h in hits] == [3, 99]
    assert not hits[0].get("reranked")


def test_local_rerank_warm_is_noop_when_already_loaded():
    reranker = LocalReranker(model_path="dummy")
    reranker._model = object()     # 视为已加载
    reranker.warm()                # 不应再去 from_pretrained


def test_build_reranker_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("RAG_RERANK_ENABLED", "false")
    assert build_reranker() is None


def test_build_reranker_auto_falls_back_to_llm_without_model(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_RERANK_ENABLED", raising=False)
    monkeypatch.setenv("RAG_RERANK_BACKEND", "auto")
    monkeypatch.setenv("RAG_RERANK_MODEL", str(tmp_path / "missing"))
    assert isinstance(build_reranker(), LLMReranker)


def test_build_reranker_local_without_model_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_RERANK_ENABLED", raising=False)
    monkeypatch.setenv("RAG_RERANK_BACKEND", "local")
    monkeypatch.setenv("RAG_RERANK_MODEL", str(tmp_path / "missing"))
    assert build_reranker() is None


def test_build_reranker_prefers_local_when_model_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_RERANK_ENABLED", raising=False)
    monkeypatch.setenv("RAG_RERANK_BACKEND", "auto")
    model_dir = tmp_path / "reranker"
    model_dir.mkdir()
    monkeypatch.setenv("RAG_RERANK_MODEL", str(model_dir))
    reranker = build_reranker()
    assert isinstance(reranker, LocalReranker)
    assert reranker.model_path == str(model_dir)
