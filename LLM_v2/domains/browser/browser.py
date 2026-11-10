# ============================================================
# LLM_v2/domains/browser/browser.py — browser 域核心库 + 域类
# ============================================================
# 迁自 LLM/browser/browser.py（核心库）+ LLM/browser/browser_world.py（域类）
# ============================================================

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Transceiver,
    CapabilityType,
)
from LLM_v2.domains.browser.browser_tools import BROWSER_TOOL_NAMES, BROWSER_TOOLS_SCHEMA

logger = logging.getLogger(__name__)


# ============ 核心库 ============

async def _agent():
    from tool.edge_agent import edge_agent
    await edge_agent.ensure_started()
    return edge_agent


async def navigate(url: str) -> dict:
    from tool.edge_agent import browser_navigate
    return await browser_navigate(url)


async def snapshot() -> dict:
    from tool.edge_agent import browser_snapshot
    return await browser_snapshot()


async def click(ref: str) -> dict:
    from tool.edge_agent import browser_click
    return await browser_click(ref)


async def type_text(ref: str, text: str, clear_first: bool = True,
                    auto_submit: bool = False) -> dict:
    from tool.edge_agent import browser_type
    return await browser_type(ref, text, bool(clear_first), bool(auto_submit))


async def press_key(key: str) -> dict:
    from tool.edge_agent import browser_press_key
    return await browser_press_key(key)


async def scroll(direction: str = "down", amount: int = 500) -> dict:
    from tool.edge_agent import browser_scroll
    return await browser_scroll(direction, int(amount))


async def tabs(action: str, ref: str = "", url: str = "") -> dict:
    from tool.edge_agent import browser_tabs
    return await browser_tabs(action, ref=ref, url=url)


async def get_content() -> dict:
    try:
        agent = await _agent()
        page = agent._page
        text = await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 3000) : ''")
        return {"success": True, "url": page.url, "title": await page.title(),
                "content": text}
    except Exception as e:
        return {"success": False, "error": f"读页面内容失败: {e}"}


async def screenshot() -> dict:
    try:
        agent = await _agent()
        snap = await agent.snapshot(include_screenshot=True)
        b64 = snap.get("screenshot_base64", "")
        if not b64:
            return {"success": False, "error": "截图失败"}
        save_dir = "./screenshots"
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception:
            save_dir = "."
        fname = f"br_{int(time.time() * 1000)}.png"
        fpath = os.path.join(save_dir, fname)
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(b64))
        return {"success": True, "url": snap["url"], "title": snap["title"],
                "image_base64": b64, "saved": fpath,
                "note": "image_base64 可交给视觉模型分析"}
    except Exception as e:
        return {"success": False, "error": f"截图失败: {e}"}


async def wait(seconds: float = 1.0) -> dict:
    await asyncio.sleep(max(0.1, float(seconds)))
    return {"success": True, "waited": seconds}


async def stop() -> dict:
    try:
        from tool.edge_agent import edge_agent
        await edge_agent.stop()
        return {"success": True, "stopped": "浏览器已关闭（下次操作自动重启）"}
    except Exception as e:
        return {"success": False, "error": f"关闭浏览器失败: {e}"}


async def handle(method: str, params: dict) -> dict:
    """子进程引擎分派入口（异步）"""
    p = params or {}
    if method == "br_navigate":
        return await navigate(p.get("url", ""))
    if method == "br_snapshot":
        return await snapshot()
    if method == "br_get_content":
        return await get_content()
    if method == "br_click":
        return await click(p.get("ref", ""))
    if method == "br_type":
        return await type_text(p.get("ref", ""), p.get("text", ""),
                               p.get("clear_first", True),
                               p.get("auto_submit", False))
    if method == "br_press_key":
        return await press_key(p.get("key", ""))
    if method == "br_scroll":
        return await scroll(p.get("direction", "down"), p.get("amount", 500))
    if method == "br_tabs":
        return await tabs(p.get("action", "list"), p.get("ref", ""), p.get("url", ""))
    if method == "br_screenshot":
        return await screenshot()
    if method == "br_wait":
        return await wait(p.get("seconds", 1.0))
    if method == "br_stop":
        return await stop()
    raise ValueError(f"未知方法: {method}")


# ============ 域 Prompt ============

_BROWSER_PROMPT = """可用工具（受控 Edge 浏览器，DOM 级）：
- br_navigate: 导航到 URL，自动等待加载并返回快照
- br_snapshot: 获取当前页面可交互元素列表（@eN 引用）+ URL/Title
- br_get_content: 读取当前页面正文文本（前 3000 字）
- br_click: 点击 @eN 元素，点击后自动返回新快照
- br_type: 在输入框填文本（auto_submit=True 自动回车提交）
- br_press_key: 按键（Enter/Tab/Esc/Arrow*/F5/Ctrl+R 等）
- br_scroll: 滚动页面（up/down/left/right，默认下滚 500px）
- br_tabs: 标签页管理（list/new/switch/close）
- br_screenshot: 截取当前页面（base64）
- br_wait: 等待若干秒（默认 1 秒）
- br_stop: 关闭浏览器实例（下次操作自动重启）

执行原则：
1. 操作前先 br_snapshot 看页面，操作完已自动返回新快照，不必再调用。
2. 点击 / 输入优先用 @eN 引用（DOM 级），不要靠坐标或截图猜位置。
3. 视频/长页面用 br_scroll；网络慢 / 弹窗用 br_wait 等加载完成。
4. 单次操作前先观察后动手，循环执行"看→点→看→点"。
"""


# ============ 域类 ============

class BrowserDomain(SubprocessCapabilityDomain, Transceiver):
    """受控 Edge 浏览器域（V2 子进程域，H-2 Transceiver 类型）"""

    id = "browser"
    label = "受控 Edge 浏览器"
    prefix = "br_"
    default_enabled = False     # 默认关闭（依赖 msedge.exe + Playwright）
    type = CapabilityType.TRANSCEIVER

    call_timeout = 60.0
    tool_timeouts = {
        "br_navigate": 30.0,
        "br_snapshot": 15.0,
        "br_screenshot": 30.0,
        "br_wait": 30.0,
        "br_stop": 10.0,
    }

    def inbound_event_types(self):
        """Browser 能力声明：感知类工具（br_snapshot）触发 PERCEPTION_UPDATED"""
        return ["perception_updated"]

    def outbound_event_types(self):
        return []

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "browser_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(BROWSER_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in BROWSER_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _BROWSER_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：msedge.exe + playwright 是否就绪"""
        try:
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
                capture_output=True, text=True, timeout=5,
            )
            if "msedge.exe" in out.stdout:
                return None
            return "msedge.exe 未运行（browser 域需 Edge 浏览器）"
        except Exception:
            return None

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_browser_domain() -> BrowserDomain:
    return BrowserDomain()


__all__ = ['BrowserDomain', 'build_browser_domain', 'handle',
           'navigate', 'snapshot', 'click', 'type_text', 'press_key',
           'scroll', 'tabs', 'get_content', 'screenshot', 'wait', 'stop']