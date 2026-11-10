# ============================================================
# LLM_v2/domains/desktop_pet/desktop_pet_engine.py
# ============================================================
from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)


def handle(method: str, params: dict):
    try:
        from LLM.desktop_pet import pet_core
    except ImportError:
        return {"error": "pet_core 不可用"}
    if method == "pet_status":
        return pet_core.status()
    if method == "pet_show":
        return pet_core.show()
    if method == "pet_hide":
        return pet_core.hide()
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
                result = {"ready": True, "engine": "desktop_pet", "protocol_version": 2}
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