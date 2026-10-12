#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desktop_pet 子进程引擎：JSON 行式协议，分派 pet_* 工具。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("pet_engine")

from LLM.desktop_pet import pet_core


def handle(method: str, params: dict):
    if method.startswith("pet_"):
        method = method[len("pet_"):]
    if method == "ping":
        return {"ok": True, "pong": "desktop-pet-engine"}
    if method == "start":
        return pet_core.pet_start(with_input=bool(params.get("with_input", True)))
    if method == "stop":
        return pet_core.pet_stop()
    if method == "status":
        return pet_core.pet_status()
    if method == "__shutdown__":
        pet_core.pet_stop()
        return {"stopping": True}
    raise ValueError(f"未知方法: {method}")


def main():
    if "--smoke" in sys.argv:
        print(json.dumps({"result": {"smoke": "ok", "methods": [
            "start", "stop", "status"]}, "id": None}, ensure_ascii=False))
        return 0
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            result = handle(req.get("method"), req.get("params") or {})
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
