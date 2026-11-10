# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/domains/research/research_engine.py — research 域子进程入口
# ============================================================
# JSON 行式 stdin/stdout 协议（与 SubprocessCapabilityDomain 对齐）。
# 复用 tool/browser_tool.py 的 web_search / fetch_webpage。
# ============================================================

from __future__ import annotations

import asyncio
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from persona.learning.store import LearningStore
from tool.browser_tool import browser_manager, fetch_webpage, web_search
from tool import research_cache as rc

_store: LearningStore | None = None


def _get_store() -> LearningStore:
    global _store
    if _store is None:
        _store = LearningStore(os.path.join(_PROJECT_ROOT, "persona", "state.db"))
    return _store


async def _call(method: str, p: dict):
    if method == "wr_search":
        query = str(p.get("query") or "")
        max_results = int(p.get("max_results") or 5)
        cached = rc.get_search(f"{query}|{max_results}")
        if cached:
            cached["cached"] = True
            return cached
        result = await web_search(query=query, max_results=max_results)
        if isinstance(result, dict) and result.get("success"):
            rc.put_search(f"{query}|{max_results}", result)
        return result

    if method == "wr_read":
        url = str(p.get("url") or "")
        max_chars = int(p.get("max_chars") or 3000)
        cached = rc.get_page(url)
        if cached and str(cached.get("content") or ""):
            return {
                "success": True,
                "url": cached.get("url", url),
                "title": cached.get("title", ""),
                "content": str(cached["content"])[:max_chars],
                "cached": True,
            }
        result = await fetch_webpage(
            url=url,
            extract_mode="article",
            max_length=max(max_chars, 8000),
        )
        if result.get("success"):
            payload = {
                "success": True,
                "url": result.get("url", ""),
                "title": result.get("title", ""),
                "content": result.get("content", ""),
            }
            rc.put_page(url, payload)
            return {
                "success": True,
                "url": payload["url"],
                "title": payload["title"],
                "content": payload["content"][:max_chars],
            }
        return result

    if method == "wr_note_save":
        sources = p.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        note = _get_store().save_note(
            topic=str(p.get("topic") or ""),
            summary=str(p.get("summary") or ""),
            sources=sources,
            tags=str(p.get("tags") or ""),
        )
        return {"success": bool(note), "note": note}

    if method == "wr_notes":
        notes = _get_store().recent_notes(
            query=str(p.get("query") or "") or None,
            limit=int(p.get("limit") or 5),
        )
        return {"success": True, "notes": notes}

    raise ValueError(f"未知方法: {method}")


async def _main_loop_async():
    loop = asyncio.get_event_loop()
    while True:
        raw = await loop.run_in_executor(None, sys.stdin.readline)
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        req_id = None
        try:
            req = json.loads(raw)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params") or {}
            if method == "__handshake__":
                result = {"ready": True, "engine": "research", "protocol_version": 2}
            elif method == "__shutdown__":
                result = {"stopping": True}
            else:
                result = await _call(method, params)
            sys.stdout.write(json.dumps({"result": result, "id": req_id},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if method == "__shutdown__":
                break
        except Exception as e:  # noqa: BLE001
            sys.stdout.write(json.dumps({"error": str(e), "id": req_id},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()

    try:
        await browser_manager.stop()
    except Exception:  # noqa: BLE001
        pass


def main():
    if "--smoke" in sys.argv:
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    asyncio.run(_main_loop_async())


if __name__ == "__main__":
    main()
