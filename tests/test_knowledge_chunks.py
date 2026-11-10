# -*- coding: utf-8 -*-
"""knowledge_chunks 分块/入库/检索与 RAG 合并测试。"""
from __future__ import annotations

import asyncio

from RAG.knowledge_chunks import (
    KnowledgeChunkManager,
    KnowledgeDocStore,
    chunk_text,
)


def test_chunk_text_respects_size_and_overlap():
    text = ("A" * 100) + "\n\n" + ("B" * 100)
    chunks = chunk_text(text, max_chars=150, overlap=20)
    assert len(chunks) == 2
    assert all(len(c) <= 150 for c in chunks)
    assert chunks[1].startswith("A" * 20)


def test_chunk_text_deduplicates_identical_chunks():
    part = "X" * 80
    chunks = chunk_text(part + "\n\n" + part, max_chars=100, overlap=0)
    assert len(chunks) == 1


def test_build_chunks_keeps_page_section_and_hash():
    pages = [{"page": 3, "section": "第一章", "text": "内容" * 300}]
    chunks = KnowledgeChunkManager.build_chunks(
        "doc1", "测试书", pages, source_url="https://example.com",
        source_type="book", tags=["小说"])
    assert chunks
    assert all(c["page_from"] == 3 for c in chunks)
    assert all(c["section"] == "第一章" for c in chunks)
    assert len({c["id"] for c in chunks}) == len(chunks)
    assert len({c["hash"] for c in chunks}) == len(chunks)
    assert chunks[0]["source_type"] == "book"


def test_doc_store_roundtrip(tmp_path):
    store = KnowledgeDocStore(tmp_path / "knowledge.db")
    store.upsert({
        "doc_id": "d1", "title": "书", "file_name": "book.pdf",
        "file_ext": ".pdf", "source_type": "book", "hash": "h1",
        "chunk_count": 3, "status": "indexed",
    })
    item = store.get("d1")
    assert item["title"] == "书"
    assert store.list()["total"] == 1
    assert store.stats()["chunks"] == 3
    store.delete("d1")
    assert store.get("d1") is None


class FakeClient:
    def __init__(self):
        self.rows = []
        self.deleted = []

    def has_collection(self, name):
        return True

    def get_load_state(self, collection_name):
        return "Loaded"

    def load_collection(self, collection_name):
        return None

    def delete(self, collection_name, filter):
        self.deleted.append(filter)

    def insert(self, collection_name, data):
        self.rows.extend(data)

    def flush(self, collection_name):
        return None

    def get_collection_stats(self, collection_name):
        return {"row_count": len(self.rows)}

    def search(self, **kwargs):
        return [{
            "entity": {
                "doc_id": "doc1", "chunk_index": 0,
                "content": "维尔薇的测试知识", "title": "测试书",
                "source_url": "", "source_type": "manual",
                "page_from": 0, "page_to": 0, "section": "",
                "hash": "h",
            },
            "distance": 0.8,
        }]


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[0.0] * 2560 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 2560


class FailingEmbedder:
    def embed_documents(self, texts):
        raise RuntimeError("embedding down")

    def embed_query(self, text):
        raise RuntimeError("embedding down")


class FakeMinio:
    def upload_file(self, *args, **kwargs):
        return "obj/1", 10

    def delete_file(self, *args, **kwargs):
        return True


def _manager(tmp_path, embedder=None, client=None):
    return KnowledgeChunkManager(
        client=client or FakeClient(),
        embedder=embedder or FakeEmbedder(),
        store=KnowledgeDocStore(tmp_path / "knowledge.db"),
        minio=FakeMinio(),
    )


def test_add_text_and_search(tmp_path):
    manager = _manager(tmp_path)
    result = manager.add_text(
        "这是一段关于维尔薇的测试知识，用来验证分块入库和检索。" * 3,
        title="测试书", tags=["测试"])
    assert result["success"] is True
    assert manager._client.rows
    assert manager.get_document(result["doc_id"])["title"] == "测试书"
    hits = manager.search("维尔薇", top_k=1)
    assert hits and hits[0]["score"] == 0.8
    assert hits[0]["title"] == "测试书"


def test_embedding_failure_writes_nothing(tmp_path):
    manager = _manager(tmp_path, embedder=FailingEmbedder())
    result = manager.add_text("失败时不应该入库", title="失败")
    assert result["success"] is False
    assert "embedding failed" in result["error"]
    assert manager._client.rows == []


def test_rag_adapter_returns_history_only():
    """知识库资料归思维脑，RAGAdapter 只拼历史对话。"""
    from LLM_v2.core.rag_adapter import RAGAdapter

    adapter = RAGAdapter()

    async def fake_history(query, top_k=3):
        return [{"content": "历史对话", "message_type": "user_message",
                 "score": 0.5}]

    adapter.retrieve = fake_history
    context = asyncio.run(adapter.build_context("问题"))
    assert "【历史相关对话】" in context
    assert "【知识库资料】" not in context
