# ============================================================
# LLM_v2/domains/browser/browser_engine.py — 受控 Edge 子进程入口
# ============================================================
# 注：browser 是 5 个域里唯一用 asyncio 的引擎（Playwright 事件循环绑定）。
# 迁自 LLM/browser/browser_engine.py；engine 接口保持 V1 兼容。
# ============================================================

from __future__ import annotations

import asyncio
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from LLM_v2.domains.browser import browser as B


def _drain_one(reader):
    """从 stdin 读一行（用 run_in_executor 避免 asyncio 子进程 readline 绑定）"""
    import asyncio as _aio
    return _aio.get_event_loop().run_in_executor(None, reader.readline)


async def _main_loop_async():
    loop = asyncio.get_event_loop()
    while True:
        # 阻塞读 stdin 一行（用 executor）
        raw = await loop.run_in_executor(None, sys.stdin.readline)
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            method = req.get("method")
            params = req.get("params") or {}
            if method == "__handshake__":
                result = {"ready": True, "engine": "browser", "protocol_version": 2}
            elif method == "__shutdown__":
                result = {"stopping": True}
            else:
                # 调用 browser.py 中对应的 sync 函数（异步）
                result = await B.handle(method, params)
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
    asyncio.run(_main_loop_async())


if __name__ == "__main__":
    main()