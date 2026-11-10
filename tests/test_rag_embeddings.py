# -*- coding: utf-8 -*-
"""嵌入失败必须抛出，禁止随机向量；查询缓存只调用一次 API。"""
from __future__ import annotations

import pytest

from RAG.Millvus_base import DoubaoEmbeddings


class _Embeddings:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("api down")
        data = [type("Item", (), {"embedding": [0.1] * 2560})() for _ in kwargs["input"]]
        return type("Resp", (), {"data": data})()


class _Client:
    def __init__(self, fail=False):
        self.embeddings = _Embeddings(fail=fail)


def test_embed_query_uses_cache():
    client = _Client()
    embedder = DoubaoEmbeddings(client=client)
    a = embedder.embed_query("同一句")
    b = embedder.embed_query("同一句")
    assert a == b
    assert client.embeddings.calls == 1


def test_embed_query_failure_raises():
    embedder = DoubaoEmbeddings(client=_Client(fail=True))
    with pytest.raises(RuntimeError):
        embedder.embed_query("查询")


def test_embed_documents_failure_raises():
    embedder = DoubaoEmbeddings(client=_Client(fail=True))
    with pytest.raises(RuntimeError):
        embedder.embed_documents(["文档"])
