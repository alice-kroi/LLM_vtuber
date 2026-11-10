# -*- coding: utf-8 -*-
"""knowledge_chunks.py — 面向书籍/网页/文档的分块知识库

P0 目标：
- 文档按页/段落切块，避免“一篇文档一个向量”
- 块级元数据（doc_id/page/section/hash）支持引用与去重
- 嵌入失败直接失败，绝不写随机向量
- 加载状态缓存 + 失败重载，避免每次检索都 load_collection

集合：knowledge_chunks（Milvus 2.6，HNSW + 标量索引）
文档元数据：persona/knowledge.db（SQLite）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import configparser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymilvus import DataType

from RAG.Millvus_base import init_milvus_client
from RAG.RAG_node import get_embedding_model
from RAG.hybrid import (
    BM25, cosine, l2_normalize, lexical_terms, rrf_fuse, tokenize)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_collection_name() -> str:
    env = (os.getenv("KNOWLEDGE_COLLECTION") or "").strip()
    if env:
        return env
    cfg_path = PROJECT_ROOT / "config.ini"
    if cfg_path.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(cfg_path, encoding="utf-8")
            if cfg.has_section("rag"):
                return cfg.get("rag", "knowledge_collection",
                               fallback="knowledge_chunks")
        except Exception:
            pass
    return "knowledge_chunks"


KNOWLEDGE_COLLECTION = _load_collection_name()
VECTOR_DIM = 2560
CHUNK_MAX_CHARS = int(os.getenv("KNOWLEDGE_CHUNK_CHARS", "1000"))
CHUNK_OVERLAP = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "150"))
EMBED_BATCH = int(os.getenv("KNOWLEDGE_EMBED_BATCH", "16"))
INSERT_BATCH = 500
KNOWLEDGE_DB = Path(os.getenv(
    "KNOWLEDGE_DB", str(PROJECT_ROOT / "persona" / "knowledge.db")))


def _rag_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = PROJECT_ROOT / "config.ini"
    if path.exists():
        try:
            cfg.read(path, encoding="utf-8")
        except Exception:
            pass
    return cfg


_RAG_CFG = _rag_cfg()


def _cfg_bool(env_name: str, key: str, default: bool) -> bool:
    env = (os.getenv(env_name) or "").strip()
    if env:
        return env.lower() == "true"
    try:
        return _RAG_CFG.getboolean("rag", key, fallback=default)
    except Exception:
        return default


def _cfg_int(env_name: str, key: str, default: int) -> int:
    env = (os.getenv(env_name) or "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            return default
    try:
        return _RAG_CFG.getint("rag", key, fallback=default)
    except Exception:
        return default


def _cfg_float(env_name: str, key: str, default: float) -> float:
    env = (os.getenv(env_name) or "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            return default
    try:
        return _RAG_CFG.getfloat("rag", key, fallback=default)
    except Exception:
        return default


_SUPPORTED_TEXT = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm"}
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])\s*")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_long_unit(unit: str, max_chars: int) -> List[str]:
    """把超长段落按句号/换行切开，仍超长则硬切。"""
    pieces: List[str] = []
    for line in unit.split("\n"):
        line = line.strip()
        if not line:
            continue
        sentences = [s.strip() for s in _SENTENCE_RE.split(line) if s.strip()]
        for sentence in sentences:
            if len(sentence) <= max_chars:
                pieces.append(sentence)
            else:
                for i in range(0, len(sentence), max_chars):
                    pieces.append(sentence[i:i + max_chars])
    return pieces


def _tail_context(text: str, overlap: int) -> str:
    """取尾部重叠片段；若起点落在词中间，裁剪到下一个空白，避免块以半个词开头。"""
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    if len(text) > overlap and not text[-(overlap + 1)].isspace():
        match = re.search(r"\s", tail)
        if match:
            tail = tail[match.end():]
    return tail.strip()


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    """按段落/句子切块，带尾部重叠；返回去重后的块。"""
    text = _normalize(text)
    if not text:
        return []
    max_chars = max(120, int(max_chars))
    overlap = max(0, min(int(overlap), max_chars // 2))

    units: List[str] = []
    for para in _split_paragraphs(text):
        if len(para) <= max_chars:
            units.append(para)
        else:
            units.extend(_split_long_unit(para, max_chars))

    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else current + "\n" + unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
        tail = _tail_context(current, overlap) if current else ""
        merged = (tail + "\n" + unit).strip() if tail else unit
        current = merged if len(merged) <= max_chars else unit

    if current.strip():
        chunks.append(current.strip())

    # 去重并保持顺序
    seen = set()
    result: List[str] = []
    for chunk in chunks:
        key = _sha(chunk)
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


class KnowledgeDocStore:
    """文档级元数据（SQLite），Milvus 只存块。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or KNOWLEDGE_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_docs (
                    doc_id       TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    file_name    TEXT,
                    file_ext     TEXT,
                    source_type  TEXT,
                    source_url   TEXT,
                    hash         TEXT,
                    chunk_count  INTEGER DEFAULT 0,
                    status       TEXT DEFAULT 'indexed',
                    error        TEXT,
                    minio_object TEXT,
                    bucket       TEXT,
                    metadata     TEXT,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_docs_updated
                    ON knowledge_docs(updated_at);
            """)

    def upsert(self, doc: Dict[str, Any]) -> None:
        fields = [
            "doc_id", "title", "file_name", "file_ext", "source_type",
            "source_url", "hash", "chunk_count", "status", "error",
            "minio_object", "bucket", "metadata",
        ]
        values = [doc.get(f) for f in fields]
        values[-1] = json.dumps(doc.get("metadata") or {}, ensure_ascii=False)
        placeholders = ",".join("?" for _ in fields)
        updates = ",".join(f"{f}=excluded.{f}" for f in fields[1:])
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO knowledge_docs ({','.join(fields)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(doc_id) DO UPDATE SET {updates}, "
                f"updated_at=CURRENT_TIMESTAMP",
                values,
            )

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM knowledge_docs WHERE doc_id=?", (doc_id,)
            ).fetchone()
        return self._dict(row) if row else None

    def list(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                "SELECT COUNT(*) FROM knowledge_docs").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM knowledge_docs ORDER BY updated_at DESC "
                "LIMIT ? OFFSET ?", (max(1, limit), max(0, offset))
            ).fetchall()
        return {"total": int(total), "data": [self._dict(r) for r in rows]}

    def delete(self, doc_id: str) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM knowledge_docs WHERE doc_id=?", (doc_id,))

    def stats(self) -> Dict[str, Any]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM knowledge_docs GROUP BY status"
            ).fetchall()
            chunks = conn.execute(
                "SELECT COALESCE(SUM(chunk_count),0) FROM knowledge_docs"
            ).fetchone()[0]
        return {
            "documents": sum(int(r[1]) for r in rows),
            "chunks": int(chunks),
            "by_status": {str(r[0]): int(r[1]) for r in rows},
        }

    @staticmethod
    def _dict(row: Any) -> Dict[str, Any]:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except Exception:
            item["metadata"] = {}
        return item


class KnowledgeChunkManager:
    """分块知识库：入库、检索、删除、统计。"""

    def __init__(self, client=None, embedder=None, store=None, minio=None,
                 collection: str = KNOWLEDGE_COLLECTION):
        self.collection = collection
        self._client = client
        self._embedder = embedder
        self._store = store or KnowledgeDocStore()
        self._minio = minio
        self._loaded = False
        self._lock = threading.Lock()
        # 混合检索：dense 候选池 + 词法候选（BM25 重排 + RRF 融合）
        self._hybrid_enabled = _cfg_bool("RAG_HYBRID_ENABLED", "hybrid_enabled", True)
        self._hybrid_pool = max(1, _cfg_int("RAG_HYBRID_POOL", "hybrid_pool", 40))
        self._hybrid_lexical_limit = max(1, _cfg_int(
            "RAG_HYBRID_LEXICAL_LIMIT", "hybrid_lexical_limit", 200))
        self._hybrid_max_terms = max(1, _cfg_int(
            "RAG_HYBRID_MAX_TERMS", "hybrid_max_terms", 10))
        self._hybrid_rrf_k = max(1, _cfg_int("RAG_HYBRID_RRF_K", "hybrid_rrf_k", 60))
        self._hybrid_lexical_weight = max(0.0, _cfg_float(
            "RAG_HYBRID_LEXICAL_WEIGHT", "hybrid_lexical_weight", 0.1))
        self._hybrid_lexical_min_ratio = min(1.0, max(0.0, _cfg_float(
            "RAG_HYBRID_LEXICAL_MIN_RATIO", "hybrid_lexical_min_ratio", 0.5)))

    # ---------- 依赖懒加载 ----------

    def _get_client(self):
        if self._client is None:
            self._client = init_milvus_client()
        return self._client

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = get_embedding_model()
        return self._embedder

    def _get_minio(self):
        if self._minio is None:
            from RAG.knowledge_base import MinIOManager
            self._minio = MinIOManager()
        return self._minio

    # ---------- 集合与加载 ----------

    def ensure_collection(self) -> None:
        client = self._get_client()
        if client.has_collection(self.collection):
            return
        with self._lock:
            if client.has_collection(self.collection):
                return
            schema = client.create_schema(
                auto_id=False, enable_dynamic_field=True)
            schema.add_field("id", DataType.VARCHAR, is_primary=True,
                             max_length=64)
            schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
            schema.add_field("chunk_index", DataType.INT32)
            schema.add_field("parent_id", DataType.VARCHAR, max_length=128)
            schema.add_field("content", DataType.VARCHAR, max_length=8192)
            schema.add_field("content_vector", DataType.FLOAT_VECTOR,
                             dim=VECTOR_DIM)
            schema.add_field("title", DataType.VARCHAR, max_length=512)
            schema.add_field("source_url", DataType.VARCHAR, max_length=2048)
            schema.add_field("source_type", DataType.VARCHAR, max_length=32)
            schema.add_field("page_from", DataType.INT32)
            schema.add_field("page_to", DataType.INT32)
            schema.add_field("section", DataType.VARCHAR, max_length=512)
            schema.add_field("language", DataType.VARCHAR, max_length=16)
            schema.add_field("tags", DataType.ARRAY, element_type=DataType.VARCHAR,
                             max_capacity=64, max_length=128)
            schema.add_field("hash", DataType.VARCHAR, max_length=64)
            schema.add_field("created_at", DataType.TIMESTAMPTZ)
            schema.add_field("metadata", DataType.JSON)

            client.create_collection(
                collection_name=self.collection,
                schema=schema,
                consistency_level="Bounded",
            )

            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name="content_vector",
                index_type="HNSW",
                index_name="content_vector_index",
                metric_type="COSINE",
                params={"M": 32, "efConstruction": 200},
            )
            for field in ("doc_id", "source_type", "hash"):
                index_params.add_index(field_name=field, index_type="INVERTED")
            index_params.add_index(
                field_name="created_at", index_type="STL_SORT")
            client.create_index(
                collection_name=self.collection, index_params=index_params)
            client.load_collection(collection_name=self.collection)
            self._loaded = True
            logger.info(f"[knowledge] 创建集合 {self.collection}")

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        client = self._get_client()
        try:
            state = client.get_load_state(collection_name=self.collection)
            state_str = str(state)
            if "Loaded" not in state_str:
                client.load_collection(collection_name=self.collection)
            self._loaded = True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[knowledge] 加载状态检查失败，尝试直接加载: {e}")
            try:
                client.load_collection(collection_name=self.collection)
                self._loaded = True
            except Exception as e2:  # noqa: BLE001
                logger.warning(f"[knowledge] 集合加载失败: {e2}")

    def _invalidate_loaded(self) -> None:
        self._loaded = False

    # ---------- 解析与分块 ----------

    @staticmethod
    def parse_file(file_data: bytes, filename: str) -> List[Dict[str, Any]]:
        """返回 [{"page": int, "section": str, "text": str}, ...]。"""
        ext = Path(filename).suffix.lower()
        pages: List[Dict[str, Any]] = []
        if ext == ".pdf":
            try:
                import fitz
                doc = fitz.open(stream=file_data, filetype="pdf")
                for idx, page in enumerate(doc, start=1):
                    text = _normalize(page.get_text() or "")
                    if text:
                        pages.append({"page": idx, "section": "", "text": text})
                doc.close()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[knowledge] PDF 解析失败 {filename}: {e}")
        elif ext == ".docx":
            try:
                import docx
                from io import BytesIO
                doc = docx.Document(BytesIO(file_data))
                section = ""
                buf: List[str] = []
                for para in doc.paragraphs:
                    text = (para.text or "").strip()
                    if not text:
                        continue
                    style = getattr(getattr(para, "style", None), "name", "") or ""
                    if style.lower().startswith("heading"):
                        if buf:
                            pages.append({"page": 0, "section": section,
                                          "text": "\n\n".join(buf)})
                            buf = []
                        section = text
                    else:
                        buf.append(text)
                if buf:
                    pages.append({"page": 0, "section": section,
                                  "text": "\n\n".join(buf)})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[knowledge] DOCX 解析失败 {filename}: {e}")
        elif ext in _SUPPORTED_TEXT:
            raw = file_data.decode("utf-8", errors="ignore")
            if ext in {".html", ".htm"}:
                try:
                    from bs4 import BeautifulSoup
                    raw = BeautifulSoup(raw, "html.parser").get_text("\n")
                except Exception:
                    raw = re.sub(r"<[^>]+>", "\n", raw)
            section = ""
            buf: List[str] = []
            for line in raw.splitlines():
                match = _HEADING_RE.match(line.strip())
                if match:
                    if buf:
                        pages.append({"page": 0, "section": section,
                                      "text": "\n\n".join(buf)})
                        buf = []
                    section = match.group(2).strip()
                else:
                    buf.append(line)
            if buf:
                pages.append({"page": 0, "section": section,
                              "text": "\n\n".join(buf)})
        else:
            try:
                raw = file_data.decode("utf-8", errors="ignore")
                if raw.strip():
                    pages.append({"page": 0, "section": "", "text": raw})
            except Exception:
                pass
        return [p for p in pages if p.get("text", "").strip()]

    @classmethod
    def build_chunks(cls, doc_id: str, title: str, pages: List[Dict[str, Any]],
                     source_url: str = "", source_type: str = "upload",
                     tags: Optional[Sequence[str]] = None,
                     language: str = "zh") -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        seen = set()
        index = 0
        for page in pages:
            page_no = int(page.get("page") or 0)
            section = str(page.get("section") or "")
            for text in chunk_text(page.get("text", "")):
                digest = _sha(text)
                if digest in seen:
                    continue
                seen.add(digest)
                chunks.append({
                    "id": f"{doc_id}:{index}",
                    "doc_id": doc_id,
                    "chunk_index": index,
                    "parent_id": f"{doc_id}:{section or 'body'}",
                    "content": text,
                    "title": title,
                    "source_url": source_url,
                    "source_type": source_type,
                    "page_from": page_no,
                    "page_to": page_no,
                    "section": section,
                    "language": language,
                    "tags": list(tags or []),
                    "hash": digest,
                })
                index += 1
        return chunks

    # ---------- 入库 ----------

    def _embed_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        embedder = self._get_embedder()
        texts = [c["content"] for c in chunks]
        vectors: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            vectors.extend(embedder.embed_documents(batch))
        if len(vectors) != len(chunks):
            raise RuntimeError(
                f"embedding 数量不匹配: {len(vectors)} != {len(chunks)}")
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != VECTOR_DIM:
                raise RuntimeError(
                    f"embedding 维度不匹配: {len(vector)} != {VECTOR_DIM}")
            chunk["content_vector"] = vector

    def _upload_raw(self, file_data: bytes, filename: str,
                    uploaded_by: str) -> Tuple[str, str]:
        try:
            minio = self._get_minio()
            object_name, _ = minio.upload_file(
                file_data, filename,
                metadata={"uploaded_by": uploaded_by,
                          "source": "knowledge_chunks"},
            )
            from RAG.knowledge_base import KNOWLEDGE_BUCKET
            return object_name, KNOWLEDGE_BUCKET
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[knowledge] 原始文件上传失败（继续索引）: {e}")
            return "", ""

    def add_document(self, file_data: bytes, filename: str,
                     title: str = "", uploaded_by: str = "system",
                     tags: Optional[Sequence[str]] = None,
                     source_url: str = "", source_type: str = "upload",
                     language: str = "zh") -> Dict[str, Any]:
        file_hash = hashlib.sha256(file_data).hexdigest()
        doc_id = _sha(f"{source_type}|{filename}|{file_hash}")[:24]
        title = (title or Path(filename).stem)[:512]
        pages = self.parse_file(file_data, filename)
        if not pages:
            return {"success": False, "error": "没有可索引的文本内容"}
        chunks = self.build_chunks(
            doc_id, title, pages, source_url=source_url,
            source_type=source_type, tags=tags, language=language)
        if not chunks:
            return {"success": False, "error": "分块后没有内容"}

        try:
            self._embed_chunks(chunks)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[knowledge] 文档嵌入失败，未入库: {e}")
            return {"success": False, "error": f"embedding failed: {e}"}

        minio_object, bucket = self._upload_raw(file_data, filename, uploaded_by)
        try:
            self._replace_chunks(doc_id, chunks)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[knowledge] 写入 Milvus 失败: {e}")
            return {"success": False, "error": f"milvus insert failed: {e}"}

        self._store.upsert({
            "doc_id": doc_id,
            "title": title,
            "file_name": filename,
            "file_ext": Path(filename).suffix.lower(),
            "source_type": source_type,
            "source_url": source_url,
            "hash": file_hash,
            "chunk_count": len(chunks),
            "status": "indexed",
            "minio_object": minio_object,
            "bucket": bucket,
            "metadata": {"uploaded_by": uploaded_by, "tags": list(tags or [])},
        })
        return {"success": True, "doc_id": doc_id, "title": title,
                "chunks": len(chunks), "pages": len(pages)}

    def add_text(self, text: str, title: str = "", source_type: str = "manual",
                 source_url: str = "", tags: Optional[Sequence[str]] = None,
                 language: str = "zh",
                 uploaded_by: str = "system") -> Dict[str, Any]:
        text = _normalize(text)
        if not text:
            return {"success": False, "error": "文本为空"}
        text_hash = _sha(text)
        doc_id = _sha(f"{source_type}|{title}|{text_hash}")[:24]
        title = (title or text[:60])[:512]
        pages = [{"page": 0, "section": "", "text": text}]
        chunks = self.build_chunks(
            doc_id, title, pages, source_url=source_url,
            source_type=source_type, tags=tags, language=language)
        try:
            self._embed_chunks(chunks)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[knowledge] 文本嵌入失败，未入库: {e}")
            return {"success": False, "error": f"embedding failed: {e}"}
        try:
            self._replace_chunks(doc_id, chunks)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"milvus insert failed: {e}"}
        self._store.upsert({
            "doc_id": doc_id,
            "title": title,
            "file_name": "",
            "file_ext": "",
            "source_type": source_type,
            "source_url": source_url,
            "hash": text_hash,
            "chunk_count": len(chunks),
            "status": "indexed",
            "metadata": {"uploaded_by": uploaded_by, "tags": list(tags or [])},
        })
        return {"success": True, "doc_id": doc_id, "title": title,
                "chunks": len(chunks)}

    def _replace_chunks(self, doc_id: str, chunks: List[Dict[str, Any]]) -> None:
        self.ensure_collection()
        client = self._get_client()
        try:
            client.delete(collection_name=self.collection,
                          filter=f'doc_id == "{doc_id}"')
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[knowledge] 删除旧块失败（可能是首次）: {e}")
        rows = []
        for chunk in chunks:
            row = {k: v for k, v in chunk.items()}
            # Milvus TIMESTAMPTZ 在行式插入时要求字符串
            row["created_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            row["metadata"] = {"source_type": chunk["source_type"]}
            rows.append(row)
        for start in range(0, len(rows), INSERT_BATCH):
            client.insert(collection_name=self.collection,
                          data=rows[start:start + INSERT_BATCH])
        try:
            client.flush(collection_name=self.collection)
        except Exception:
            pass
        self._loaded = False
        self._ensure_loaded()

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 5,
               score_threshold: float = 0.30,
               source_type: Optional[str] = None,
               doc_id: Optional[str] = None,
               hybrid: Optional[bool] = None) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        self.ensure_collection()
        self._ensure_loaded()
        vector = self._get_embedder().embed_query(query)
        filters = []
        if source_type:
            filters.append(f'source_type == "{source_type}"')
        if doc_id:
            filters.append(f'doc_id == "{doc_id}"')
        expr = " and ".join(filters) if filters else ""

        use_hybrid = self._hybrid_enabled if hybrid is None else bool(hybrid)
        pool = max(int(top_k), self._hybrid_pool) if use_hybrid else int(top_k)
        dense_hits = self._dense_search(vector, pool, expr, score_threshold)
        if not use_hybrid:
            return dense_hits[:top_k]

        lexical_hits = self._lexical_search(query, vector, expr, score_threshold)
        if not lexical_hits:
            return dense_hits[:top_k]
        return self._fuse(dense_hits, lexical_hits, top_k)

    def _dense_search(self, vector: List[float], limit: int, expr: str,
                      score_threshold: float) -> List[Dict[str, Any]]:
        client = self._get_client()
        for attempt in range(2):
            try:
                results = client.search(
                    collection_name=self.collection,
                    data=[vector],
                    anns_field="content_vector",
                    limit=max(1, int(limit)),
                    output_fields=[
                        "doc_id", "chunk_index", "content", "title",
                        "source_url", "source_type", "page_from", "page_to",
                        "section", "hash",
                    ],
                    filter=expr or None,
                    search_params={
                        "metric_type": "COSINE",
                        "params": {"ef": max(64, int(limit))},
                    },
                )
                return self._parse_hits(results, score_threshold, limit)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[knowledge] 向量检索失败（尝试 {attempt+1}/2）: {e}")
                self._invalidate_loaded()
                if attempt == 0:
                    self._ensure_loaded()
                else:
                    return []
        return []

    def _lexical_search(self, query: str, vector: List[float], expr: str,
                        score_threshold: float) -> List[Dict[str, Any]]:
        """用 `content like` 拉词法候选，再在候选池内做 BM25 + 相似度估值。"""
        terms = lexical_terms(query, self._hybrid_max_terms)
        if not terms:
            return []
        likes = " or ".join(f'content like "%{t}%"' for t in terms)
        filter_expr = f"({likes})"
        if expr:
            filter_expr = f"({filter_expr}) and ({expr})"
        client = self._get_client()
        try:
            rows = client.query(
                collection_name=self.collection,
                filter=filter_expr,
                output_fields=[
                    "doc_id", "chunk_index", "content", "content_vector",
                    "title", "source_url", "source_type", "page_from",
                    "page_to", "section", "hash",
                ],
                limit=max(1, int(self._hybrid_lexical_limit)),
            )
        except Exception as e:  # noqa: BLE001
            # 客户端不支持 query / 集合无 like 能力时退回纯向量检索
            logger.info(f"[knowledge] 词法候选不可用，退回纯向量检索: {e}")
            return []
        if not rows:
            return []

        docs_tokens = [tokenize(str(row.get("content") or "")) for row in rows]
        bm25 = BM25(docs_tokens)
        lexical_scores = bm25.scores(self._discriminative_tokens(query, docs_tokens))
        # 只保留相对池内最强词法命中足够接近的块，弱命中不扰动向量排序
        strongest = max(lexical_scores) if lexical_scores else 0.0
        lexical_cutoff = strongest * self._hybrid_lexical_min_ratio
        query_norm = l2_normalize(vector)
        hits: List[Dict[str, Any]] = []
        for row, lexical in zip(rows, lexical_scores):
            if lexical <= 0 or lexical < lexical_cutoff:
                continue
            chunk_vector = row.get("content_vector")
            similarity = (cosine(query_norm, chunk_vector)
                          if isinstance(chunk_vector, (list, tuple)) else 0.0)
            if similarity < score_threshold:
                continue
            hit = self._row_to_hit(row, similarity)
            hit["lexical_score"] = round(float(lexical), 4)
            hits.append(hit)
        hits.sort(key=lambda item: -item["lexical_score"])
        return hits

    def _fuse(self, dense_hits: List[Dict[str, Any]],
              lexical_hits: List[Dict[str, Any]],
              top_k: int) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {
            self._hit_key(hit): hit for hit in dense_hits}
        for hit in lexical_hits:
            key = self._hit_key(hit)
            if key in merged:
                merged[key]["lexical_score"] = hit["lexical_score"]
            else:
                merged[key] = hit
        fused = rrf_fuse(
            [[self._hit_key(hit) for hit in dense_hits],
             [self._hit_key(hit) for hit in lexical_hits]],
            k=self._hybrid_rrf_k,
            weights=[1.0, self._hybrid_lexical_weight],
        )
        for key, hit in merged.items():
            hit["fused_score"] = round(fused.get(key, 0.0), 6)
            hit.setdefault("lexical_score", 0.0)
        ordered = sorted(
            merged.values(), key=lambda hit: (-hit["fused_score"], -hit["score"]))
        return ordered[:max(1, int(top_k))]

    @staticmethod
    def _discriminative_tokens(query: str,
                               docs_tokens: List[List[str]]) -> List[str]:
        """去掉在候选池里过于常见的查询词（human/model/language 之类）。

        候选池本身已是同一主题的相关块，通用词只会把所有块拉平；
        全部词都过于常见时返回空，让词法这一路自动失效。
        """
        tokens = list(dict.fromkeys(tokenize(query)))
        if not tokens or not docs_tokens:
            return tokens
        counts: Counter = Counter()
        for doc_tokens in docs_tokens:
            counts.update(set(doc_tokens))
        limit = max(0.5 * len(docs_tokens), 2.0)
        return [t for t in tokens if counts.get(t, 0) <= limit]

    @staticmethod
    def _hit_key(hit: Dict[str, Any]) -> str:
        return f"{hit.get('doc_id', '')}:{hit.get('chunk_index', 0)}"

    @staticmethod
    def _row_to_hit(entity: Dict[str, Any], score: float) -> Dict[str, Any]:
        return {
            "score": round(float(score), 4),
            "doc_id": entity.get("doc_id", ""),
            "chunk_index": entity.get("chunk_index", 0),
            "content": entity.get("content", ""),
            "title": entity.get("title", ""),
            "source_url": entity.get("source_url", ""),
            "source_type": entity.get("source_type", ""),
            "page_from": entity.get("page_from", 0),
            "page_to": entity.get("page_to", 0),
            "section": entity.get("section", ""),
            "hash": entity.get("hash", ""),
        }

    @classmethod
    def _parse_hits(cls, results, threshold: float, top_k: int) -> List[Dict[str, Any]]:
        # Milvus 通常返回 [[hit, ...]]；测试替身可能直接返回 [hit, ...]
        if results and isinstance(results[0], dict):
            hits = results
        else:
            hits = results[0] if results else []
        items: List[Dict[str, Any]] = []
        for hit in hits:
            entity = hit.get("entity", {}) if isinstance(hit, dict) else getattr(hit, "entity", {})
            distance = hit.get("distance", 1.0) if isinstance(hit, dict) else getattr(hit, "distance", 1.0)
            # Milvus 的 COSINE 指标返回的是相似度（越大越相似）
            score = float(distance)
            if score < threshold:
                continue
            items.append(cls._row_to_hit(entity, score))
        items.sort(key=lambda x: -x["score"])
        return items[:top_k]

    # ---------- 管理 ----------

    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        self.ensure_collection()
        client = self._get_client()
        doc = self._store.get(doc_id)
        try:
            client.delete(collection_name=self.collection,
                          filter=f'doc_id == "{doc_id}"')
            client.flush(collection_name=self.collection)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        if doc and doc.get("minio_object"):
            try:
                self._get_minio().delete_file(
                    doc["minio_object"], doc.get("bucket") or "knowledge-files")
            except Exception:
                pass
        self._store.delete(doc_id)
        return {"success": True, "doc_id": doc_id}

    def list_documents(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        return self._store.list(limit=limit, offset=offset)

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(doc_id)

    def stats(self) -> Dict[str, Any]:
        data = self._store.stats()
        try:
            client = self._get_client()
            if client.has_collection(self.collection):
                stats = client.get_collection_stats(self.collection)
                data["milvus_rows"] = int(stats.get("row_count", 0))
        except Exception:
            data["milvus_rows"] = 0
        data["collection"] = self.collection
        return data


_manager: Optional[KnowledgeChunkManager] = None
_manager_lock = threading.Lock()


def get_knowledge_chunk_manager() -> KnowledgeChunkManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = KnowledgeChunkManager()
        return _manager
