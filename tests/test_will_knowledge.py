"""思维脑知识件：检索发布、共享缓存读取、知识缺口测试。"""
from __future__ import annotations

import asyncio
import time

from LLM_v2.core.will_knowledge import WillKnowledge

ITEM = {
    "content": "RLHF 先用人类偏好训练奖励模型，再用强化学习微调策略。",
    "title": "对齐综述", "page_from": 12, "score": 0.83,
}


class FakeManager:
    def __init__(self, items=None, error: str = ""):
        self.items = items if items is not None else [ITEM]
        self.error = error
        self.calls = []

    def search(self, query, top_k=3):
        self.calls.append((query, top_k))
        if self.error:
            raise RuntimeError(self.error)
        return self.items


class NoopReranker:
    """测试用：不做重排，避免真的调用模型。"""

    candidates = 3

    async def rerank(self, query, items, top_k=3):
        return list(items)[:top_k]


class WarmReranker:
    candidates = 3

    def __init__(self):
        self.warmed = 0

    def warm(self):
        self.warmed += 1


def _knowledge(manager, **kwargs) -> WillKnowledge:
    kwargs.setdefault("reranker", NoopReranker())
    return WillKnowledge(manager=manager, **kwargs)


def test_publish_then_read_returns_context_with_citation():
    async def run():
        knowledge = _knowledge(FakeManager(), wait_sec=0.2)
        items, error = await knowledge.search("RLHF 是什么")
        assert items and not error
        await knowledge.publish("RLHF 是什么", items)
        return await knowledge.read("RLHF 是什么")

    text = asyncio.run(run())
    assert "【知识库资料】" in text
    assert "对齐综述 p.12" in text
    assert "score=0.83" in text


def test_read_waits_for_concurrent_publish():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=1.0)

        async def publish_later():
            await asyncio.sleep(0.05)
            await knowledge.publish("问题", [ITEM])

        task = asyncio.create_task(publish_later())
        text = await knowledge.read("问题")
        await task
        return text

    assert "对齐综述 p.12" in asyncio.run(run())


def test_read_returns_empty_on_timeout():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=0.05)
        return await knowledge.read("没人检索过的问题")

    assert asyncio.run(run()) == ""


def test_read_uses_short_grace_when_nothing_pending():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=1.0,
                               grace_sec=0.05)
        started = time.monotonic()
        text = await knowledge.read("没人检索的问题")
        return text, time.monotonic() - started

    text, elapsed = asyncio.run(run())
    assert text == ""
    assert elapsed < 0.5


def test_read_waits_past_grace_when_retrieval_pending():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=1.0,
                               grace_sec=0.05)
        knowledge.begin("问题")

        async def publish_later():
            await asyncio.sleep(0.2)
            await knowledge.publish("问题", [ITEM])

        task = asyncio.create_task(publish_later())
        text = await knowledge.read("问题")
        await task
        return text

    assert "对齐综述 p.12" in asyncio.run(run())


def test_key_is_normalized_ignoring_space_case_and_punctuation():
    async def run():
        knowledge = _knowledge(FakeManager(), wait_sec=0.0)
        await knowledge.publish("RLHF  是什么？", [ITEM])
        return await knowledge.read("rlhf是什么")

    assert "对齐综述 p.12" in asyncio.run(run())


def test_weak_hit_records_gap_and_injects_nothing():
    weak = dict(ITEM, score=0.42)

    async def run():
        knowledge = _knowledge(FakeManager(), wait_sec=0.0,
                               gap_min_score=0.76)
        await knowledge.publish("红烧肉需要炖多久", [weak])
        return await knowledge.read("红烧肉需要炖多久"), knowledge.recent_gaps()

    text, gaps = asyncio.run(run())
    assert text == ""
    assert gaps and gaps[0]["topic"] == "红烧肉需要炖多久"


def test_no_hit_records_gap_and_publishes_empty_text():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=0.0)
        items, error = await knowledge.search("量子纠缠的退相干时间")
        text = await knowledge.publish("量子纠缠的退相干时间", items)
        return text, knowledge.recent_gaps()

    text, gaps = asyncio.run(run())
    assert text == ""
    assert gaps and gaps[0]["topic"] == "量子纠缠的退相干时间"


def test_success_clears_gap():
    async def run():
        knowledge = _knowledge(FakeManager([]), wait_sec=0.0)
        await knowledge.publish("某话题", [])
        assert knowledge.recent_gaps()
        await knowledge.publish("某话题", [ITEM])
        return knowledge.recent_gaps()

    assert asyncio.run(run()) == []


def test_search_failure_returns_error_without_raising():
    async def run():
        knowledge = _knowledge(FakeManager(error="milvus down"),
                               wait_sec=0.0)
        return await knowledge.search("任意问题")

    items, error = asyncio.run(run())
    assert items == []
    assert "milvus down" in error


def test_disabled_knowledge_skips_search():
    async def run():
        knowledge = _knowledge(FakeManager(), wait_sec=0.0)
        knowledge._enabled = False
        return await knowledge.search("任意问题")

    items, error = asyncio.run(run())
    assert items == []
    assert error == ""


def test_warm_skips_reranker_by_default(monkeypatch):
    monkeypatch.delenv("RAG_RERANK_WARMUP", raising=False)
    fake = WarmReranker()
    knowledge = WillKnowledge(manager=FakeManager(), reranker=fake)
    knowledge.warm()
    assert fake.warmed == 0


def test_warm_loads_reranker_when_enabled(monkeypatch):
    monkeypatch.setenv("RAG_RERANK_WARMUP", "true")
    fake = WarmReranker()
    knowledge = WillKnowledge(manager=FakeManager(), reranker=fake)
    knowledge.warm()
    assert fake.warmed == 1


def test_needs_knowledge_filter():
    from LLM_v2.brains.will_engine import WillEngineBrain

    assert WillEngineBrain._needs_knowledge("RLHF 是什么？")
    assert WillEngineBrain._needs_knowledge("解释一下思维链")
    assert not WillEngineBrain._needs_knowledge("哈哈哈太有意思了")
    assert not WillEngineBrain._needs_knowledge("晚上好呀")


def test_expired_entry_is_not_returned():
    async def run():
        knowledge = _knowledge(FakeManager(), wait_sec=0.0, ttl_sec=0.0)
        await knowledge.publish("过期问题", [ITEM])
        return await knowledge.read("过期问题")

    assert asyncio.run(run()) == ""
