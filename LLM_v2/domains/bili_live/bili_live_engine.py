# ============================================================
# LLM_v2/domains/bili_live/bili_live_engine.py — B 站直播间监听子进程入口
# ============================================================
# 注：B 站直播间监听是 blivedm 客户端，本身不是"工具域"，
# 这里把它作为 bili_live_* 工具（start/stop/status/observe）的执行载体。
# ============================================================

from __future__ import annotations

import json
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from LLM_v2.domains.bili_live.bili_live_listener import BiliListener


_listener: BiliListener | None = None
_lock = threading.Lock()


def _get_listener():
    global _listener
    with _lock:
        if _listener is None:
            _listener = BiliListener()
    return _listener


def handle(method: str, params: dict):
    p = params or {}
    listener = _get_listener()
    if method == "__handshake__":
        return {"ready": True, "engine": "bili_live", "protocol_version": 2}
    if method == "bili_live_start":
        return listener.start(room_ids=p.get("room_ids"))
    if method == "bili_live_stop":
        return listener.stop()
    if method == "bili_live_status":
        return listener.status()
    if method == "bili_live_observe":
        return listener.observe(limit=p.get("limit", 20))
    if method == "__shutdown__":
        try:
            listener.stop()
        except Exception:
            pass
        return {"stopping": True}
    raise ValueError(f"未知方法: {method}")


def _main_loop():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            method = req.get("method")
            result = handle(method, req.get("params") or {})
            sys.stdout.write(json.dumps({"result": result, "id": req.get("id")},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if method == "__shutdown__":
                break
        except Exception as e:
            sys.stdout.write(json.dumps({"error": str(e), "id": None},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    if "--smoke" in sys.argv:
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    _main_loop()


if __name__ == "__main__":
    main()
