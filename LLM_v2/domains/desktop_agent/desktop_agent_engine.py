# ============================================================
# LLM_v2/domains/desktop_agent/desktop_agent_engine.py
# ============================================================
from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)


def handle(method: str, params: dict):
    """桌面代理域子进程引擎分派（迁自 V1 LLM/desktop_agent/desktop_agent.py）"""
    p = params or {}
    try:
        from LLM.desktop_agent import desktop_agent as DA
    except ImportError:
        return {"error": "desktop_agent 底层库不可用"}
    if method == "dsk_windows":
        return DA.list_windows(limit=p.get("limit", 30))
    if method == "dsk_activate":
        return DA.activate_window(p.get("title_keyword", ""))
    if method == "dsk_list_elements":
        return DA.list_elements(p.get("window_title", ""), limit=p.get("limit", 40))
    if method == "dsk_click_xy":
        return DA.click_xy(int(p.get("x", 0)), int(p.get("y", 0)),
                            clicks=p.get("clicks", 1),
                            button=p.get("button", "left"))
    if method == "dsk_status":
        return DA.status()
    return {"error": f"未知方法: {method}"}


def _main_loop():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            method = req.get("method")
            if method == "__handshake__":
                result = {"ready": True, "engine": "desktop_agent", "protocol_version": 2}
            elif method == "__shutdown__":
                result = {"stopping": True}
            else:
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