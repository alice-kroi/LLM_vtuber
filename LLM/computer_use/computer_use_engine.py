#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""computer_use 引擎子进程入口（JSON 行式 stdin/stdout 协议，与 SubprocessSkillWorld 对齐）。

支持 --smoke：启动即回一条打通 JSON 后退出。方法：ping / cu_*。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM.computer_use import computer_use as C  # noqa: E402


class ComputerUseEngine:
    def __init__(self, config: dict):
        self.config = config or {}

    def handle(self, method: str, params: dict):
        p = params or {}
        if method == "ping":
            return {"ok": True, "pong": "computer-use-engine"}
        if method == "__shutdown__":
            return {"stopping": True}
        if not method.startswith("cu_"):
            raise ValueError(f"未知方法: {method}")
        op = method[len("cu_"):]
        if op == "find_file":
            return C.find_file(p.get("keyword", ""), p.get("root", "C:\\"),
                               p.get("depth", 6), p.get("limit", 20))
        if op == "open_folder":
            return C.open_folder(p.get("target", ""))
        if op == "open_file":
            return C.open_file(p.get("path", ""))
        if op == "launch_app":
            return C.launch_app(p.get("app", ""))
        if op == "list_apps":
            return C.list_apps(include_registry=bool(p.get("include_registry", False)))
        if op == "list_directory":
            return C.list_directory(p.get("path", ""))
        if op == "type_text":
            return C.type_text(p.get("text", ""))
        if op == "press_key":
            return C.press_key(p.get("hotkey", ""))
        if op == "click_xy":
            return C.click_xy(p.get("x", 0), p.get("y", 0),
                              p.get("clicks", 1), p.get("button", "left"))
        if op == "clipboard_get":
            return C.clipboard_get()
        if op == "clipboard_set":
            return C.clipboard_set(p.get("text", ""))
        if op == "list_processes":
            return C.list_processes(p.get("keyword", ""))
        if op == "list_windows":
            return C.list_windows()
        if op == "foreground":
            return C.foreground(p.get("title_keyword", ""))
        if op == "minimize_window":
            return C.minimize_window(p.get("title_keyword", ""))
        if op == "restore_window":
            return C.restore_window(p.get("title_keyword", ""))
        if op == "show_desktop":
            return C.show_desktop()
        if op == "check":
            return C.check(p.get("check_type", ""), p.get("target", ""),
                           p.get("expect", "true"))
        raise ValueError(f"未知方法: {method}")


def _main_loop(engine: ComputerUseEngine):
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
    engine = ComputerUseEngine({})
    _main_loop(engine)


if __name__ == "__main__":
    main()
