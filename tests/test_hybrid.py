"""混合检索：分词、BM25、RRF 与 knowledge search 融合测试。"""
from __future__ import annotations

from RAG.hybrid import BM25, lexical_terms, rrf_fuse, tokenize
from RAG.knowledge_chunks import KnowledgeChunkManager, KnowledgeDocStore


def test_tokenize_english_and_chinese():
    en = tokenize("What is chain-of-thought prompting?")
    assert "chain" in en and "thought" in en and "prompting" in en
    assert "what" not in en and "is" not in en

    zh = tokenize("什么是思维链提示？")
    assert "思维" in zh and "维链" in zh
    assert "什么" not in zh


def test_lexical_terms_keeps_case_variants_and_limits():
    terms = lexical_terms("RLHF 是怎么让大模型对齐人类偏好", max_terms=6)
    assert "RLHF" in terms and "rlhf" in terms
    assert len(terms) <= 6
    assert len(set(terms)) == len(terms)


def test_bm25_ranks_matching_document_first():
    docs = [
        tokenize("the quick brown fox jumps"),
        tokenize("向量数据库检索与混合检索优化"),
        tokenize("unrelated content about weather"),
    ]
    scores = BM25(docs).scores(tokenize("混合检索"))
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


def test_rrf_fuse_accumulates_ranks():
    fused = rrf_fuse([["a", "b"], ["b"]], k=60)
    assert fused["a"] == 1 / 61
    assert fused["b"] > fused["a"]


def test_rrf_fuse_respects_weights():
    fused = rrf_fuse([["a", "b"], ["b"]], k=60, weights=[1.0, 0.25])
    assert fused["a"] == 1 / 61
    assert fused["b"] == 1 / 62 + 0.25 / 61

    muted = rrf_fuse([["a", "b"], ["b"]], k=60, weights=[1.0, 0.0])
    assert muted["b"] == 1 / 62


class DenseHitClient:
    """dense 返回 A/B/C，词法候选只返回 C。"""

    def __init__(self, rows=None):
        self.rows = rows or []

    def has_collection(self, name):
        return True

    def get_load_state(self, collection_name):
        return "Loaded"

    def load_collection(self, collection_name):
        return None

    def search(self, **kwargs):
        def hit(doc_id, score):
            return {
                "entity": {
                    "doc_id": doc_id, "chunk_index": 0, "content": f"{doc_id} 内容",
                    "title": "书", "source_url": "", "source_type": "manual",
                    "page_from": 1, "page_to": 1, "section": "", "hash": doc_id,
                },
                "distance": score,
            }
        return [[hit("a", 0.80), hit("b", 0.78), hit("c", 0.77)]]

    def query(self, **kwargs):
        return self.rows


class TinyEmbedder:
    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0, 0.0]


def _manager(tmp_path, client):
    return KnowledgeChunkManager(
        client=client,
        embedder=TinyEmbedder(),
        store=KnowledgeDocStore(tmp_path / "knowledge.db"),
    )


def _lexical_row(doc_id, content, vector):
    return {
        "doc_id": doc_id, "chunk_index": 0, "content": content,
        "content_vector": vector, "title": "书", "source_url": "",
        "source_type": "manual", "page_from": 1, "page_to": 1,
        "section": "", "hash": doc_id,
    }


def test_hybrid_promotes_lexical_match(tmp_path):
    client = DenseHitClient(rows=[
        _lexical_row("c", "chain of thought prompting improves reasoning",
                     [0.9, 0.1, 0.0, 0.0]),
    ])
    hits = _manager(tmp_path, client).search("chain of thought", top_k=3)
    assert [h["doc_id"] for h in hits][0] == "c"
    assert hits[0]["lexical_score"] > 0
    assert hits[0]["fused_score"] > 0
    # score 仍是 dense 相似度，融合只改排序
    assert hits[0]["score"] == 0.77


def test_hybrid_disabled_is_pure_dense(tmp_path):
    client = DenseHitClient(rows=[
        _lexical_row("c", "chain of thought", [0.9, 0.1, 0.0, 0.0]),
    ])
    hits = _manager(tmp_path, client).search(
        "chain of thought", top_k=3, hybrid=False)
    assert [h["doc_id"] for h in hits] == ["a", "b", "c"]
    assert "fused_score" not in hits[0]


def test_hybrid_falls_back_when_lexical_query_unsupported(tmp_path):
    class NoQueryClient(DenseHitClient):
        def query(self, **kwargs):
            raise RuntimeError("query unsupported")

    hits = _manager(tmp_path, NoQueryClient()).search("chain of thought", top_k=3)
    assert [h["doc_id"] for h in hits] == ["a", "b", "c"]
    assert "lexical_score" not in hits[0]
