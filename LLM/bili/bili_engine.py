#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili 子进程引擎：JSON 行式协议，分派 bili_* 工具到监听器。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("bili_engine")

from LLM.bili.bili_listener import BiliListener

_listener: BiliListener | None = None


def _get_listener(params: dict) -> BiliListener:
    global _listener
    if _listener is None:
        _listener = BiliListener(
            room_ids=params.get("room_ids") or None,
            sessdata=params.get("sessdata", ""),
            output_port=int(params.get("output_port", 8081)),
            forward=bool(params.get("forward", True)),
        )
    return _listener


async def _handle(method: str, params: dict):
    if method.startswith("bili_"):
        method = method[len("bili_"):]
    if method == "ping":
        return {"ok": True, "pong": "bili-engine"}
    if method == "start":
        listener = _get_listener(params)
        return await listener.start()
    if method == "stop":
        return await _get_listener(params).stop()
    if method == "status":
        return _get_listener(params).status()
    if method == "observe":
        return {"success": True, **(_get_listener(params).status()),
                "messages": _get_listener(params).recent_messages(
                    int(params.get("limit", 20)))}
    if method == "__shutdown__":
        if _listener is not None:
            await _listener.stop()
        return {"stopping": True}
    raise ValueError(f"未知方法: {method}")


async def main_async():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            result = await _handle(req.get("method"), req.get("params") or {})
            sys.stdout.write(json.dumps({"result": result, "id": req.get("id")},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if req.get("method") == "__shutdown__":
                break
        except Exception as e:
            rid = req.get("id") if "req" in locals() else None
            sys.stdout.write(json.dumps({"error": str(e), "id": rid},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    if "--smoke" in sys.argv:
        print(json.dumps({"result": {"smoke": "ok", "methods": [
            "start", "stop", "status", "observe"]}, "id": None}, ensure_ascii=False))
        return 0
    import asyncio
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    sys.exit(main())
