# ============================================================
# LLM_v2/domains/ui_perception/ui_perception_engine.py
# ============================================================
from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)


def handle(method: str, params: dict):
    """UI 感知域子进程引擎（迁自 V1 LLM/ui_perception/ui_perception.py）"""
    p = params or {}
    try:
        from LLM.ui_perception import ui_perception as UP
    except ImportError:
        return {"error": "ui_perception 底层库不可用"}
    if method == "uip_list_windows":
        return UP.list_windows(limit=p.get("limit", 30))
    if method == "uip_read_tree":
        return UP.read_tree(p.get("window_title", ""), depth=p.get("depth", 5))
    if method == "uip_locate":
        return UP.locate(p.get("window_title", ""), p.get("name", ""),
                         control_type=p.get("control_type", ""))
    if method == "uip_ocr":
        return UP.ocr(p.get("window_title", ""))
    if method == "uip_ground":
        return UP.ground(p.get("window_title", ""), p.get("description", ""))
    if method == "uip_capture":
        return UP.capture(p.get("window_title", ""))
    if method == "uip_status":
        return UP.status()
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
                result = {"ready": True, "engine": "ui_perception", "protocol_version": 2}
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