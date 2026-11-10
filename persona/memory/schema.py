# ============================================================
# persona/memory/schema.py — 记忆库 Schema
# ============================================================
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mem_items (
    id TEXT PRIMARY KEY,
    track TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'project',
    scope_key TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    summary TEXT DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'agent',
    session_id TEXT DEFAULT '',
    turn_id TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    hash TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT DEFAULT '',
    use_count INTEGER NOT NULL DEFAULT 0,
    decay REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_mem_items_track ON mem_items(track, status);
CREATE INDEX IF NOT EXISTS idx_mem_items_scope ON mem_items(scope, scope_key, status);
CREATE INDEX IF NOT EXISTS idx_mem_items_hash ON mem_items(hash);

CREATE TABLE IF NOT EXISTS turn_summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'private',
    user_id TEXT DEFAULT '',
    project_key TEXT DEFAULT '',
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    entities_json TEXT DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_summaries_session ON turn_summaries(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_turn_summaries_visibility ON turn_summaries(visibility, created_at);
CREATE INDEX IF NOT EXISTS idx_turn_summaries_user ON turn_summaries(user_id, created_at);

CREATE TABLE IF NOT EXISTS mem_proposals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    reason TEXT DEFAULT '',
    evidence_json TEXT DEFAULT '{}',
    frequency INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT DEFAULT '',
    decided_by TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mem_proposals_status ON mem_proposals(status, created_at);

CREATE TABLE IF NOT EXISTS mem_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    item_id TEXT DEFAULT '',
    actor TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    result TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_audit_created ON mem_audit(created_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    return conn


__all__ = ["connect", "SCHEMA_VERSION"]
