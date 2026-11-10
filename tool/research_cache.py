# ============================================================
# tool/research_cache.py — 检索缓存 + 定向站点
# ============================================================
# 减少重复联网：同一查询 / 同一页面在 TTL 内直接命中本地缓存。
# 目录：data/web_cache/search/、data/web_cache/pages/
# 定向站点：config/research_sources.json（主题关键词 → 权威站点）
# ============================================================

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = PROJECT_ROOT / "data" / "web_cache"
SEARCH_DIR = CACHE_ROOT / "search"
PAGE_DIR = CACHE_ROOT / "pages"
SOURCES_FILE = PROJECT_ROOT / "config" / "research_sources.json"

SEARCH_TTL = 7 * 24 * 3600      # 查询结果 7 天
PAGE_TTL = 30 * 24 * 3600       # 页面正文 30 天


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _digest(text: str) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:20]


def _read(path: Path, ttl: float) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(raw.get("_ts") or 0) > ttl:
            return None
        return raw.get("data")
    except Exception:  # noqa: BLE001
        return None


def _write(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"_ts": time.time(), "data": data},
                                   ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[research_cache] 写入失败 {path.name}: {e}")


# ---------- 查询缓存 ----------

def get_search(query: str, ttl: float = SEARCH_TTL) -> Optional[Dict[str, Any]]:
    return _read(SEARCH_DIR / f"{_digest(query)}.json", ttl)


def put_search(query: str, payload: Dict[str, Any]) -> None:
    _write(SEARCH_DIR / f"{_digest(query)}.json", payload)


# ---------- 页面缓存 ----------

def get_page(url: str, ttl: float = PAGE_TTL) -> Optional[Dict[str, Any]]:
    return _read(PAGE_DIR / f"{_digest(url)}.json", ttl)


def put_page(url: str, payload: Dict[str, Any]) -> None:
    _write(PAGE_DIR / f"{_digest(url)}.json", payload)


# ---------- 定向站点 ----------

def _load_sources() -> List[Dict[str, Any]]:
    try:
        if not SOURCES_FILE.is_file():
            return []
        data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
        return data.get("topics") or []
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[research_cache] 站点配置读取失败: {e}")
        return []


def site_filters(topic: str, limit: int = 3) -> List[str]:
    """按主题关键词返回 site: 过滤词（最多 limit 个站点）"""
    text = _norm(topic)
    if not text:
        return []
    for entry in _load_sources():
        keywords = [str(k).lower() for k in entry.get("keywords") or []]
        if any(k and k in text for k in keywords):
            sites = [str(s) for s in entry.get("sites") or []][:limit]
            return [f"site:{s}" for s in sites]
    return []


__all__ = ["get_search", "put_search", "get_page", "put_page",
           "site_filters", "CACHE_ROOT", "SEARCH_TTL", "PAGE_TTL"]
