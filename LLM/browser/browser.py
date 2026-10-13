#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser 域核心库：浏览器控制（受控 Edge，Playwright/CDP，DOM 级）。

覆盖能力（对齐 computer_use_research.md 阶段 D / R3）：
- 访问网页、读取页面可交互元素与正文、点击/输入/按键/滚动
- 标签页管理（列表/新建/切换/关闭）、截图、等待

复用 tool.edge_agent 的 EdgeAgent（headed Edge + @ref 语义化快照），
全部 DOM 级操作，不做"截图+坐标"。
"""

import asyncio
import base64
import os
import time


async def _agent():
    from tool.edge_agent import edge_agent
    await edge_agent.ensure_started()
    return edge_agent


async def navigate(url: str) -> dict:
    """导航到 URL（自动等待加载，返回新页面快照）。"""
    from tool.edge_agent import browser_navigate
    return await browser_navigate(url)


async def snapshot() -> dict:
    """获取当前页面可交互元素列表（带 @ref 引用）与 URL/Title。"""
    from tool.edge_agent import browser_snapshot
    return await browser_snapshot()


async def click(ref: str) -> dict:
    """点击 @ref 元素（ref 来自 snapshot 的 @eN），点击后自动返回新快照。"""
    from tool.edge_agent import browser_click
    return await browser_click(ref)


async def type_text(ref: str, text: str, clear_first: bool = True,
                    auto_submit: bool = False) -> dict:
    """在 @ref 输入框输入文本（fill 触发 onChange）；auto_submit 输完自动回车提交。"""
    from tool.edge_agent import browser_type
    return await browser_type(ref, text, bool(clear_first), bool(auto_submit))


async def press_key(key: str) -> dict:
    """在页面按键：Enter/Tab/Esc/Arrow*/Home/End/F5/Ctrl+R 等。"""
    from tool.edge_agent import browser_press_key
    return await browser_press_key(key)


async def scroll(direction: str = "down", amount: int = 500) -> dict:
    """滚动页面（up/down/left/right）。"""
    from tool.edge_agent import browser_scroll
    return await browser_scroll(direction, int(amount))


async def tabs(action: str, ref: str = "", url: str = "") -> dict:
    """标签页管理：list / new / switch / close。"""
    from tool.edge_agent import browser_tabs
    return await browser_tabs(action, ref=ref, url=url)


async def get_content() -> dict:
    """读取当前页面正文文本（innerText，前 3000 字），供 AI 理解网页内容。"""
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
    """截取当前页面（base64 + 保存 PNG），可交给视觉模型分析。"""
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
    """等待若干秒（页面加载/动画/网络请求）。"""
    await asyncio.sleep(max(0.1, float(seconds)))
    return {"success": True, "waited": seconds}


async def stop() -> dict:
    """关闭受控 Edge 浏览器实例（下次操作会自动重新启动）。"""
    try:
        from tool.edge_agent import edge_agent
        await edge_agent.stop()
        return {"success": True, "stopped": "浏览器已关闭（下次操作自动重启）"}
    except Exception as e:
        return {"success": False, "error": f"关闭浏览器失败: {e}"}
