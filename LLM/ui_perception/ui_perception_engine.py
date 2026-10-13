#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_perception 引擎子进程入口（JSON 行式 stdin/stdout 协议，与 SubprocessSkillWorld 对齐）。

支持 --smoke：启动即回一条打通 JSON 后退出。方法：ping / uip_*。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM.ui_perception import ui_perception as U  # noqa: E402


class UiPerceptionEngine:
    def __init__(self, config: dict):
        self.config = config or {}

    def handle(self, method: str, params: dict):
        p = params or {}
        if method == "ping":
            return {"ok": True, "pong": "ui-perception-engine"}
        if method == "__shutdown__":
            return {"stopping": True}
        if not method.startswith("uip_"):
            raise ValueError(f"未知方法: {method}")
        op = method[len("uip_"):]
        if op == "list_windows":
            return U.list_windows()
        if op == "dump_ui_tree":
            return U.dump_ui_tree(p.get("window_title", ""),
                                  p.get("max_depth", 8), p.get("max_items", 150))
        if op == "click_control":
            return U.click_control(p.get("window_title", ""),
                                   p.get("control_name", ""), p.get("index", 0))
        if op == "set_text":
            return U.set_text(p.get("window_title", ""), p.get("control_name", ""),
                              p.get("text", ""), p.get("index", 0))
        if op == "read_text":
            return U.read_text(p.get("window_title", ""), p.get("control_name", ""))
        if op == "ocr_screen":
            return U.ocr_screen(p.get("window_title", ""))
        if op == "ground_element":
            return U.ground_element(p.get("window_title", ""), p.get("description", ""))
        if op == "capture_window":
            return U.capture_window(p.get("window_title", ""))
        raise ValueError(f"未知方法: {method}")


def _main_loop(engine: UiPerceptionEngine):
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            result = engine.handle(req.get("method"), req.get("params") or {})
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
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    engine = UiPerceptionEngine({})
    _main_loop(engine)


if __name__ == "__main__":
    main()
