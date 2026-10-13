#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser 引擎子进程入口（JSON 行式 stdin/stdout 协议，与 SubprocessSkillWorld 对齐）。

Playwright 是异步 API 且浏览器对象绑定事件循环，因此引擎整体跑在**单个事件循环**里
（stdin 读取放线程池），不能每次调用单独 asyncio.run。

支持 --smoke：启动即回一条打通 JSON 后退出（不拉起浏览器）。方法：ping / br_*。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM.browser import browser as B  # noqa: E402


class BrowserEngine:
    def __init__(self, config: dict):
        self.config = config or {}

    async def handle(self, method: str, params: dict):
        p = params or {}
        if method == "ping":
            return {"ok": True, "pong": "browser-engine"}
        if method == "__shutdown__":
            return {"stopping": True}
        if not method.startswith("br_"):
            raise ValueError(f"未知方法: {method}")
        op = method[len("br_"):]
        if op == "navigate":
            return await B.navigate(p.get("url", ""))
        if op == "snapshot":
            return await B.snapshot()
        if op == "get_content":
            return await B.get_content()
        if op == "click":
            return await B.click(p.get("ref", ""))
        if op == "type":
            return await B.type_text(p.get("ref", ""), p.get("text", ""),
                                     p.get("clear_first", True), p.get("auto_submit", False))
        if op == "press_key":
            return await B.press_key(p.get("key", ""))
        if op == "scroll":
            return await B.scroll(p.get("direction", "down"), p.get("amount", 500))
        if op == "tabs":
            return await B.tabs(p.get("action", ""), p.get("ref", ""), p.get("url", ""))
        if op == "screenshot":
            return await B.screenshot()
        if op == "wait":
            return await B.wait(p.get("seconds", 1.0))
        if op == "stop":
            return await B.stop()
        raise ValueError(f"未知方法: {method}")


async def _main_loop(engine: BrowserEngine):
    loop = asyncio.get_running_loop()
    while True:
        # stdin 阻塞读放到线程池，主循环保持事件循环驱动 Playwright
        raw = await loop.run_in_executor(None, sys.stdin.readline)
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            result = await engine.handle(req.get("method"), req.get("params") or {})
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
    engine = BrowserEngine({})
    asyncio.run(_main_loop(engine))


if __name__ == "__main__":
    main()
