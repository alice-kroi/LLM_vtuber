#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""desktop_agent 子进程引擎：JSON 行式协议，分派 dsk_* 工具到核心库。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("dsk_engine")

from LLM.desktop_agent import desktop_agent as core


def handle(method: str, params: dict):
    # 兼容带域前缀的工具名（代理按工具名转发，如 dsk_windows）
    if method.startswith("dsk_"):
        method = method[len("dsk_"):]
    if method == "ping":
        return {"ok": True, "pong": "desktop-agent-engine"}
    if method == "available_apps":
        return core.available_apps(bool(params.get("include_registry", False)))
    if method == "foreground_window":
        return core.foreground_window()
    if method == "windows":
        return core.list_windows(bool(params.get("visible_only", True)))
    if method == "clickable":
        return core.clickable_controls(
            title_keyword=params.get("title_keyword", ""),
            hwnd=int(params.get("hwnd") or 0),
            max_items=int(params.get("max_items", 80)))
    if method == "click":
        return core.click(int(params["x"]), int(params["y"]),
                          button=params.get("button", "left"),
                          background_hwnd=int(params.get("background_hwnd") or 0))
    if method == "activate":
        return core.activate_window(
            title_keyword=params.get("title_keyword", ""),
            hwnd=int(params.get("hwnd") or 0))
    if method == "__shutdown__":
        return {"stopping": True}
    raise ValueError(f"未知方法: {method}")


def main():
    if "--smoke" in sys.argv:
        print(json.dumps({"result": {"smoke": "ok", "methods": [
            "available_apps", "foreground_window", "windows",
            "clickable", "click", "activate"]}, "id": None}, ensure_ascii=False))
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
