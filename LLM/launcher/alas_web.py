#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ALAS 网页 CDP 控制（Playwright 驱动 Edge，打开 pywebio 页面直接读 DOM 按钮并点击）。

路线：ALAS Electron 原窗被单实例锁+启动器生命周期堵死，无法在同一窗口注入 CDP。
但 ALAS 本质是 uvicorn 网页服务（默认 localhost:22267，无口令），因此改用
Playwright(channel=msedge) 打开该页，Playwright 底层即 CDP，可：
- 按文本/选择器读取全部 <button> 的文本与屏幕坐标；
- 直接对目标按钮调用 DOM click()（比坐标法更贴近真实控件）。

依赖：playwright（LLM env 已装）；系统 Edge（channel='msedge'，无需 playwright install）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ALAS_WEB_URL = "http://localhost:22267/"
PLAYWRIGHT_OK = False
try:
    from playwright.sync_api import sync_playwright  # type: ignore
    PLAYWRIGHT_OK = True
except Exception:  # pragma: no cover
    PLAYWRIGHT_OK = False


class AlasWebCdp:
    """复用单个 Edge 页面，提供 read_buttons / click。"""

    def __init__(self, url: str = ALAS_WEB_URL):
        self.url = url
        self._pw = None
        self._page = None
        self._alive = time.time()

    # ---------- 生命周期 ----------
    def ensure(self, timeout_sec: float = 45.0) -> Dict[str, Any]:
        if self._page is not None and not self._page.is_closed():
            return {"success": True, "state": "reused"}
        if not PLAYWRIGHT_OK:
            return {"success": False, "error": "playwright 未安装（LLM env）"}
        try:
            self._pw = sync_playwright().start()
            browser = self._pw.chromium.launch(channel="msedge", headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 880})
            page = ctx.new_page()
            page.goto(self.url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=20000)
            self._page = page
            self._alive = time.time()
            return {"success": True, "state": "opened", "url": page.url}
        except Exception as e:
            self.close()
            return {"success": False, "error": f"打开 ALAS 页面失败: {e}"}

    def close(self) -> None:
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._page = None

    # ---------- 读控件（DOM 按钮） ----------
    def read_buttons(self) -> Dict[str, Any]:
        r = self.ensure()
        if not r.get("success"):
            return r
        try:
            data = self._page.evaluate("""() => {
              const out = [];
              for (const b of document.querySelectorAll('button')) {
                const t = (b.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!t) continue;
                const r = b.getBoundingClientRect();
                out.push({
                  text: t, x: Math.round(r.x + r.width / 2),
                  y: Math.round(r.y + r.height / 2),
                  w: Math.round(r.width), h: Math.round(r.height),
                  visible: r.width > 0 && r.height > 0
                });
              }
              return out;
            }""")
            return {"success": True, "buttons": data}
        except Exception as e:
            return {"success": False, "error": f"读按钮失败: {e}"}

    # ---------- 点击（DOM 原生） ----------
    def click(self, text: str, partial: bool = True) -> Dict[str, Any]:
        r = self.ensure()
        if not r.get("success"):
            return r
        try:
            value = self._page.evaluate("""([text, partial]) => {
              const btns = [...document.querySelectorAll('button')];
              const norm = (s) => (s || '').trim().replace(/\\s+/g, '');
              let hit = null;
              if (!partial) hit = btns.find(b => norm(b.textContent) === text);
              if (!hit) hit = btns.find(b => norm(b.textContent).includes(text));
              if (!hit) return {ok: false};
              const r = hit.getBoundingClientRect();
              hit.click();
              return {ok: true, text: norm(hit.textContent),
                      x: Math.round(r.x + r.width / 2),
                      y: Math.round(r.y + r.height / 2)};
            }""", [text, bool(partial)])
            if value.get("ok"):
                return {"success": True, "method": "web-cdp-dom-click",
                        "button": value.get("text"),
                        "viewport_center": (value.get("x"), value.get("y"))}
            avail = [b.get("text") for b in self.read_buttons().get("buttons", [])][:20]
            return {"success": False, "error": f"页面内未命中按钮 '{text}'",
                    "available": avail}
        except Exception as e:
            return {"success": False, "error": f"网页点击失败: {e}"}


_inst: Optional[AlasWebCdp] = None


def alas_web_click(button_text: str = "启动", url: str = ALAS_WEB_URL) -> Dict[str, Any]:
    """快捷：单例驱动 ALAS 网页并点击指定文本按钮。"""
    global _inst
    if _inst is None:
        _inst = AlasWebCdp(url)
    r = _inst.click(button_text)
    if not r.get("success"):
        # 读按钮作为诊断
        diag = _inst.read_buttons()
        r["diagnose"] = diag
    return r


def alas_web_read() -> Dict[str, Any]:
    global _inst
    if _inst is None:
        _inst = AlasWebCdp()
    return _inst.read_buttons()


def alas_web_close() -> None:
    global _inst
    if _inst is not None:
        _inst.close()
        _inst = None