# -*- coding: utf-8 -*-
"""
EdgeAgent —— 像 TRAE 内置浏览器 agent 一样操控 Edge 浏览器

核心机制:
  - 持久化 Edge 实例 (Playwright channel="msedge")
  - 每次 snapshot 生成带 @ref 的 accessibility tree（语义化元素定位）
  - click/type/navigate 后自动 wait + re-snapshot
  - LLM 通过 function calling 调用 6 个工具函数

依赖: playwright (async_api), Python 3.10+
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Error as PlaywrightError,
)

logger = logging.getLogger(__name__)

# ---------- 默认配置 ----------
_DEFAULT_CHANNEL = "msedge"
_DEFAULT_MODE = "headed"          # headed / headless / cdp
_DEFAULT_CDP_URL = "http://127.0.0.1:9222"
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
_DEFAULT_TIMEOUT_MS = 30_000


# ================================================================
#  Snapshot JS —— 注入页面、构建 accessibility tree + @ref
# ================================================================

SNAPSHOT_JS = r"""() => {
    const interactive = [];
    let counter = 0;

    const isInteractive = (el) => {
        if (!el || !el.tagName) return false;
        const tag = el.tagName.toLowerCase();
        if (['button', 'a', 'input', 'select', 'textarea'].includes(tag)) return true;
        if (['[role=button]','[role=link]','[role=textbox]','[role=combobox]','[role=checkbox]','[role=radio]','[role=tab]','[role=menuitem]'].some(s => el.matches(s))) return true;
        if (el.getAttribute('contenteditable') === 'true') return true;
        if (tag === 'label' && el.control) return false;  // label 已关联的 input 会自己出现
        return false;
    };

    const getRole = (el) => {
        const r = el.getAttribute && el.getAttribute('role');
        if (r) return r;
        const tag = el.tagName.toLowerCase();
        const type = el.type && el.type.toLowerCase();
        if (tag === 'a') return 'link';
        if (tag === 'button') return 'button';
        if (tag === 'textarea') return 'textbox';
        if (tag === 'input') {
            if (type === 'checkbox') return 'checkbox';
            if (type === 'radio') return 'radio';
            if (type === 'submit' || type === 'button' || type === 'image') return 'button';
            return 'textbox';
        }
        if (tag === 'select') return 'combobox';
        return 'generic';
    };

    const getName = (el) => {
        if (!el) return '';
        if (el.getAttribute && el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        if (el.getAttribute && el.getAttribute('aria-labelledby')) {
            const labelIds = el.getAttribute('aria-labelledby').split(' ');
            const parts = labelIds.map(id => document.getElementById(id)?.innerText || '').filter(Boolean);
            if (parts.length) return parts.join(' ').trim();
        }
        if (el.placeholder) return el.placeholder;
        if (el.innerText) return el.innerText.trim().replace(/\s+/g, ' ');
        if (el.value !== undefined && el.value) return String(el.value);
        if (el.title) return el.title;
        return '';
    };

    const escapeAttr = (v) => String(v).replace(/"/g, '\\"');
    const genCss = (el) => {
        if (el.id) return '#' + escapeAttr(el.id);
        const parts = [];
        let cur = el;
        for (let i = 0; i < 4 && cur && cur.nodeType === 1; i++) {
            let sel = cur.tagName.toLowerCase();
            if (cur.className && typeof cur.className === 'string') {
                const cls = cur.className.trim().split(/\s+/).slice(0, 2).map(c => escapeAttr(c)).join('.');
                if (cls) sel += '.' + cls;
            }
            if (cur.parentElement) {
                const sibs = Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName);
                if (sibs.length > 1) sel += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
            }
            parts.unshift(sel);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };

    const walker = document.createTreeWalker(
        document.body || document.documentElement,
        NodeFilter.SHOW_ELEMENT,
        {
            acceptNode(node) {
                if (!node || node.nodeType !== 1) return NodeFilter.FILTER_REJECT;
                if (isInteractive(node)) return NodeFilter.FILTER_ACCEPT;
                return NodeFilter.FILTER_SKIP;
            }
        }
    );

    let node;
    while ((node = walker.nextNode())) {
        const rect = node.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) continue;
        const style = window.getComputedStyle(node);
        if (style.visibility === 'hidden' || style.display === 'none') continue;

        counter++;
        interactive.push({
            ref: '@e' + counter,
            role: getRole(node),
            name: getName(node).slice(0, 120),
            tag: node.tagName.toLowerCase(),
            css_path: genCss(node),
            bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            },
            enabled: !node.disabled && node.getAttribute('aria-disabled') !== 'true',
            checked: node.checked ?? node.getAttribute('aria-checked') ?? null,
            placeholder: node.placeholder || '',
            value: node.value !== undefined ? String(node.value) : '',
        });
    }
    return interactive;
}"""


# ================================================================
#  EdgeAgent 核心类
# ================================================================

class EdgeAgent:
    """
    持续化 Edge 浏览器管理器 + 语义化交互层。

    用法:
        agent = EdgeAgent.get_instance()
        await agent.start(mode="headed")
        snap = await agent.snapshot()          # → {@e1 Button 登录, ...}
        await agent.navigate("https://example.com")
        await agent.click("@e3")
        await agent.type("@e5", "hello")
        await agent.press_key("Enter")
        await agent.stop()
    """

    _instance: Optional["EdgeAgent"] = None

    def __new__(cls) -> "EdgeAgent":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "EdgeAgent":
        return cls()

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._mode: str = _DEFAULT_MODE
        self._lock = asyncio.Lock()
        self._initialized = True
        self._active_tab_ref: str = "@tab_0"
        self._tab_ref_map: Dict[str, Page] = {}
        # 惰性启动：保存启动参数，首次调用工具时才真正拉起 Edge
        self._launch_params: Dict[str, Any] = {}

    # -------------------- 惰性启动（按需） --------------------

    def configure(self, mode: str = None, cdp_url: str = None, channel: str = _DEFAULT_CHANNEL,
                  viewport: dict = None, start_maximized: bool = True) -> None:
        """仅保存 Edge 启动参数，不立即启动浏览器（实现'按需启动'）。"""
        params = {"mode": mode} if mode else {}
        if cdp_url:
            params["cdp_url"] = cdp_url
        params["channel"] = channel
        if viewport:
            params["viewport"] = viewport
        params["start_maximized"] = start_maximized
        self._launch_params.update(params)
        logger.info("[EdgeAgent] 已缓存启动参数（惰性启动），尚未拉起 Edge")

    async def ensure_started(self) -> None:
        """若 Edge 未启动则用缓存的参数启动（按需启动入口）。已在运行则直接返回。"""
        if self._browser and self._browser.is_connected():
            return
        params = dict(self._launch_params)
        if not params:
            # 无缓存参数时用默认值
            params = {"channel": _DEFAULT_CHANNEL, "start_maximized": True}
        logger.info("[EdgeAgent] 按需启动 Edge (惰性): %s", params)
        await self.start(**params)

    # -------------------- 生命周期 --------------------

    async def start(self, mode: str = None, cdp_url: str = None, channel: str = _DEFAULT_CHANNEL,
                    viewport: dict = None, start_maximized: bool = True) -> None:
        """启动 Edge 浏览器（headed/headless/cdp 三种模式）"""
        async with self._lock:
            if mode is not None:
                self._mode = mode
            # 同步保存启动参数，保证 ensure_started 语义一致
            self._launch_params = {
                "mode": self._mode,
                "channel": channel,
                "start_maximized": start_maximized,
            }
            if cdp_url:
                self._launch_params["cdp_url"] = cdp_url
            if viewport:
                self._launch_params["viewport"] = viewport
            if self._browser and self._browser.is_connected():
                logger.info("[EdgeAgent] 浏览器已在运行，跳过 start")
                return

            self._playwright = await async_playwright().start()

            if self._mode == "cdp":
                target = (cdp_url or _DEFAULT_CDP_URL).replace("localhost", "127.0.0.1")
                logger.info("[EdgeAgent] 以 CDP 模式连接 Edge → %s", target)
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(target)
                except PlaywrightError as e:
                    logger.error("[EdgeAgent] CDP 连接失败: %s", e)
                    raise
            else:
                is_headless = self._mode == "headless"
                launch_args = ["--disable-gpu", "--no-sandbox"]
                if start_maximized and not is_headless:
                    launch_args.append("--start-maximized")
                logger.info("[EdgeAgent] 启动 Edge (mode=%s, channel=%s, headless=%s)",
                            self._mode, channel, is_headless)
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=is_headless,
                        channel=channel,
                        args=launch_args,
                    )
                except PlaywrightError as e:
                    logger.warning("[EdgeAgent] Edge channel 启动失败(%s)，回退到 executable_path", e)
                    # 回退: 手动查 msedge.exe
                    import subprocess
                    result = subprocess.run(
                        ["where", "msedge"], capture_output=True, text=True, shell=True
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        edge_exe = result.stdout.strip().splitlines()[0]
                        logger.info("[EdgeAgent] 找到 Edge 路径: %s", edge_exe)
                        self._browser = await self._playwright.chromium.launch(
                            headless=is_headless,
                            executable_path=edge_exe,
                            args=launch_args,
                        )
                    else:
                        raise

            # 获取/创建 context + page
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else await self._browser.new_context(
                viewport=None if (start_maximized and self._mode != "headless") else (viewport or _DEFAULT_VIEWPORT),
                locale="zh-CN",
            )
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            self._page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
            self._rebuild_tab_map()

            logger.info("[EdgeAgent] ✅ Edge 就绪: url=%s, title=%s",
                        self._page.url, await self._page.title())

    async def stop(self) -> None:
        async with self._lock:
            try:
                if self._browser and self._mode != "cdp":
                    await self._browser.close()
            except Exception as e:
                logger.warning("[EdgeAgent] close browser 异常: %s", e)
            self._browser = None
            self._context = None
            self._page = None
            self._tab_ref_map = {}
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            logger.info("[EdgeAgent] 已停止")

    async def reset_session(self) -> None:
        """保留浏览器，只清空当前 context（等价于「清 cookies + 回到空白」）"""
        async with self._lock:
            if not self._context:
                return
            try:
                await self._context.clear_cookies()
                await self._context.clear_permissions()
            except Exception:
                pass
            await self._page.goto("about:blank")

    # -------------------- Tab 管理 --------------------

    def _rebuild_tab_map(self) -> None:
        if not self._context:
            return
        self._tab_ref_map = {}
        for i, p in enumerate(self._context.pages):
            self._tab_ref_map[f"@tab_{i}"] = p
        if self._page and self._page in self._tab_ref_map.values():
            for ref, p in self._tab_ref_map.items():
                if p is self._page:
                    self._active_tab_ref = ref
                    break

    async def list_tabs(self) -> List[Dict[str, Any]]:
        async with self._lock:
            self._rebuild_tab_map()
            out = []
            for ref, p in self._tab_ref_map.items():
                try:
                    out.append({
                        "ref": ref,
                        "url": p.url,
                        "title": await p.title(),
                        "active": p is self._page,
                    })
                except Exception:
                    continue
            return out

    async def new_tab(self, url: str = "") -> str:
        async with self._lock:
            new_p = await self._context.new_page()
            if url:
                await new_p.goto(url, wait_until="domcontentloaded")
            self._page = new_p
            self._rebuild_tab_map()
            for ref, p in self._tab_ref_map.items():
                if p is new_p:
                    self._active_tab_ref = ref
                    break
            return self._active_tab_ref

    async def switch_tab(self, ref: str) -> None:
        async with self._lock:
            target = self._tab_ref_map.get(ref)
            if not target:
                raise ValueError(f"Tab {ref} 不存在，当前 tabs: {list(self._tab_ref_map.keys())}")
            await target.bring_to_front()
            self._page = target
            self._active_tab_ref = ref

    async def close_tab(self, ref: str) -> None:
        async with self._lock:
            target = self._tab_ref_map.get(ref)
            if target is None:
                return
            await target.close()
            # 确保还有 page 活着
            if not self._context.pages:
                self._page = await self._context.new_page()
            else:
                self._page = self._context.pages[-1]
            self._rebuild_tab_map()

    # -------------------- Snapshot（核心）--------------------

    async def snapshot(self, include_screenshot: bool = False) -> Dict[str, Any]:
        """
        执行一次语义化快照：构建 accessibility tree，给每个元素分配 @ref。

        Returns:
            {
                "url": str,
                "title": str,
                "elements": [{ref, role, name, tag, css_path, bounds, enabled, checked, placeholder}, ...],
                "screenshot_base64": str (可选)
            }
        """
        async with self._lock:
            return await self._snapshot_impl(include_screenshot)

    async def _snapshot_impl(self, include_screenshot: bool = False) -> Dict[str, Any]:
        """无锁版本的 snapshot。调用者必须已持有 self._lock。"""
        if not self._page:
            raise RuntimeError("EdgeAgent 未启动，请先 await agent.start()")
        elements = await self._page.evaluate(SNAPSHOT_JS)
        title = await self._page.title()
        url = self._page.url

        screenshot_b64 = ""
        if include_screenshot:
            try:
                screenshot_bytes = await self._page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            except Exception as e:
                logger.warning("[EdgeAgent] screenshot 失败: %s", e)

        logger.info("[EdgeAgent] snapshot: url=%s, elements=%d", url, len(elements))

        return {
            "url": url,
            "title": title,
            "elements": elements,
            "screenshot_base64": screenshot_b64,
        }

    async def snapshot_for_llm(self) -> str:
        """生成给 LLM 看的纯文本快照（带 @ref 表格 + URL/Title）"""
        snap = await self.snapshot(include_screenshot=False)
        lines = []
        lines.append(f"URL: {snap['url']}")
        lines.append(f"Title: {snap['title']}")
        lines.append(f"可交互元素 ({len(snap['elements'])} 个):")
        lines.append("-" * 60)
        for el in snap["elements"]:
            name = (el.get("name") or el.get("placeholder") or "").strip()
            if not name:
                name = f"<{el['tag']}>"
            if len(name) > 50:
                name = name[:50] + "..."
            extra = []
            if not el.get("enabled"):
                extra.append("[disabled]")
            if el.get("checked") is True:
                extra.append("[✓]")
            if el.get("placeholder") and not el.get("name"):
                extra.append(f"ph={el['placeholder'][:30]}")
            suffix = (" " + " ".join(extra)) if extra else ""
            lines.append(f"  {el['ref']:<6} [{el['role']:<10}] {name}{suffix}")
        lines.append("-" * 60)
        return "\n".join(lines)

    # -------------------- 内部: @ref → Playwright locator --------------------

    async def _resolve_ref_to_locator(self, ref: str):
        """
        从 snapshot 找到 ref 对应的 css_path，转成 Playwright locator。
        注意：调用者必须已持有 self._lock。
        """
        snap = await self._snapshot_impl(include_screenshot=False)
        target = None
        for el in snap["elements"]:
            if el["ref"] == ref:
                target = el
                break
        if not target:
            raise ValueError(f"找不到元素 {ref}，当前 refs: {[e['ref'] for e in snap['elements']]}")

        css = target["css_path"]
        locator = self._page.locator(css).first
        try:
            await locator.wait_for(state="visible", timeout=3000)
        except PlaywrightError:
            raise RuntimeError(f"元素 {ref} ({css}) 当前不可见")
        return locator, target

    # -------------------- 交互原语 --------------------

    async def navigate(self, url: str) -> Dict[str, Any]:
        """导航到 URL，等待加载后返回新 snapshot"""
        async with self._lock:
            if not self._page:
                raise RuntimeError("EdgeAgent 未启动")
            logger.info("[EdgeAgent] navigate → %s", url)
            await self._page.goto(url, wait_until="domcontentloaded", timeout=_DEFAULT_TIMEOUT_MS)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightError:
                pass
            await asyncio.sleep(0.5)
            return await self._snapshot_impl(include_screenshot=False)

    async def click(self, ref: str) -> Dict[str, Any]:
        """点击 @ref 元素，自动 wait + 返回新 snapshot"""
        async with self._lock:
            if not self._page:
                raise RuntimeError("EdgeAgent 未启动")
            locator, target = await self._resolve_ref_to_locator(ref)
            logger.info("[EdgeAgent] click %s → %s '%s'", ref, target["css_path"], target.get("name", ""))
            try:
                await locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            await locator.click(timeout=_DEFAULT_TIMEOUT_MS)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightError:
                pass
            await asyncio.sleep(0.5)
            return await self._snapshot_impl(include_screenshot=False)

    async def type(self, ref: str, text: str, clear_first: bool = True, auto_submit: bool = False) -> Dict[str, Any]:
        """
        在 @ref 输入框中输入文本。
        用 locator.fill() 确保 React/Vue 的 onChange 正确触发。
        auto_submit=True 时输入完自动按 Enter 提交（搜索框/表单场景推荐）。
        若 Enter 未触发跳转，会 fallback 到最近的 <form>.submit()。
        """
        async with self._lock:
            if not self._page:
                raise RuntimeError("EdgeAgent 未启动")
            locator, target = await self._resolve_ref_to_locator(ref)
            logger.info("[EdgeAgent] type %s → %r (auto_submit=%s)", ref, text[:30], auto_submit)
            await locator.click()
            if clear_first:
                await locator.fill("")  # fill("") 清空，React 安全
            await locator.fill(text)   # fill() 自动触发 input/change 事件
            await asyncio.sleep(0.2)

            if auto_submit:
                old_url = self._page.url
                # 方法 1: 在元素上按 Enter
                try:
                    await locator.press("Enter")
                except PlaywrightError:
                    pass
                await asyncio.sleep(1.5)
                # 方法 2: 若 URL 没变，用 JS 找最近的 form 并 submit / 点搜索按钮
                if self._page.url == old_url:
                    logger.info("[EdgeAgent] Enter 未触发跳转，尝试 form.submit() / 搜索按钮")
                    css_path = target.get("css_path", "")
                    await self._page.evaluate("""
                        (css_selector) => {
                            let el = css_selector ? document.querySelector(css_selector) : null;
                            // 往上找 form
                            let cur = el;
                            while (cur && cur.tagName) {
                                if (cur.tagName.toLowerCase() === 'form') {
                                    cur.requestSubmit ? cur.requestSubmit() : cur.submit();
                                    return 'form_submitted';
                                }
                                cur = cur.parentElement;
                            }
                            // 没找到 form，试试全局搜索按钮
                            const btn = document.querySelector(
                                '.center-search__bar button, .search-btn, ' +
                                'button[type=submit], button.search, ' +
                                'div.center-search__bar button'
                            );
                            if (btn) { btn.click(); return 'button_clicked'; }
                            return 'nothing_found';
                        }
                    """, css_path)
                    await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(0.3)
            return await self._snapshot_impl(include_screenshot=False)

    async def press_key(self, key: str) -> Dict[str, Any]:
        """按下键盘"""
        async with self._lock:
            if not self._page:
                raise RuntimeError("EdgeAgent 未启动")
            valid = {"Enter", "Tab", "Escape", "Esc", "ArrowUp", "ArrowDown", "ArrowLeft",
                     "ArrowRight", "Home", "End", "PageUp", "PageDown", "Backspace",
                     "Delete", "F1", "F2", "F5", "Ctrl+R", "Ctrl+A", "Ctrl+C", "Ctrl+V"}
            if key not in valid:
                raise ValueError(f"不支持的按键 {key}，可选: {sorted(valid)}")
            logger.info("[EdgeAgent] press_key %s", key)
            await self._page.keyboard.press(key)
            await asyncio.sleep(0.5)
            return await self._snapshot_impl(include_screenshot=False)

    async def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        """滚动页面"""
        async with self._lock:
            if not self._page:
                raise RuntimeError("EdgeAgent 未启动")
            y = -amount if direction in ("up", "top") else amount
            x = -amount if direction == "left" else (amount if direction == "right" else 0)
            logger.info("[EdgeAgent] scroll %s %dpx", direction, amount)
            await self._page.mouse.wheel(x, y)
            await asyncio.sleep(0.4)
            return await self._snapshot_impl(include_screenshot=False)

    async def wait(self, seconds: float = 1.0) -> None:
        await asyncio.sleep(max(0.1, seconds))


# ---------- 全局单例 ----------
edge_agent = EdgeAgent()


# ================================================================
#  LLM function calling 工具函数（独立函数，走 tool_registry）
# ================================================================

async def browser_snapshot() -> Dict[str, Any]:
    """获取 Edge 浏览器当前页面的可交互元素列表（带 @ref 引用）。"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.snapshot(include_screenshot=False)
        text = await agent.snapshot_for_llm()
        return {
            "success": True,
            "url": snap["url"],
            "title": snap["title"],
            "text_view": text,
            "elements_count": len(snap["elements"]),
        }
    except Exception as e:
        return {"success": False, "error": f"snapshot 失败: {e}"}


async def browser_navigate(url: str) -> Dict[str, Any]:
    """导航 Edge 到指定 URL。"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.navigate(url)
        text = await agent.snapshot_for_llm()
        return {
            "success": True,
            "url": snap["url"],
            "title": snap["title"],
            "next_view": text,
        }
    except Exception as e:
        return {"success": False, "error": f"navigate 失败: {e}"}


async def browser_click(ref: str) -> Dict[str, Any]:
    """点击 Edge 页面上的元素。ref 来自 browser_snapshot 返回的 @eN 引用。"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.click(ref)
        text = await agent.snapshot_for_llm()
        return {"success": True, "next_view": text}
    except Exception as e:
        return {"success": False, "error": f"click 失败: {e}"}


async def browser_type(ref: str, text: str, clear_first: bool = True, auto_submit: bool = False) -> Dict[str, Any]:
    """在 Edge 页面的输入框中输入文本。ref 来自 browser_snapshot。auto_submit=True 时输入完自动按 Enter（适合搜索框）。"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.type(ref, text, clear_first, auto_submit)
        text_view = await agent.snapshot_for_llm()
        return {"success": True, "next_view": text_view, "typed": text, "auto_submit": auto_submit}
    except Exception as e:
        return {"success": False, "error": f"type 失败: {e}"}


async def browser_press_key(key: str) -> Dict[str, Any]:
    """在 Edge 页面上按下键盘按键: Enter/Tab/Esc/ArrowUp/ArrowDown/Home/End..."""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.press_key(key)
        text_view = await agent.snapshot_for_llm()
        return {"success": True, "next_view": text_view}
    except Exception as e:
        return {"success": False, "error": f"press_key 失败: {e}"}


async def browser_scroll(direction: str = "down", amount: int = 500) -> Dict[str, Any]:
    """滚动 Edge 页面。"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        snap = await agent.scroll(direction, amount)
        text_view = await agent.snapshot_for_llm()
        return {"success": True, "next_view": text_view}
    except Exception as e:
        return {"success": False, "error": f"scroll 失败: {e}"}


async def browser_tabs(action: str, ref: str = "", url: str = "") -> Dict[str, Any]:
    """管理 Edge 浏览器标签页。action: list/new/switch/close"""
    agent = EdgeAgent.get_instance()
    await agent.ensure_started()
    try:
        if action == "list":
            tabs = await agent.list_tabs()
            return {"success": True, "tabs": tabs}
        elif action == "new":
            new_ref = await agent.new_tab(url)
            snap = await agent.snapshot_for_llm()
            return {"success": True, "new_tab_ref": new_ref, "next_view": snap}
        elif action == "switch":
            if not ref:
                return {"success": False, "error": "switch 需要 ref"}
            await agent.switch_tab(ref)
            snap = await agent.snapshot_for_llm()
            return {"success": True, "next_view": snap}
        elif action == "close":
            if not ref:
                return {"success": False, "error": "close 需要 ref"}
            await agent.close_tab(ref)
            snap = await agent.snapshot_for_llm()
            return {"success": True, "next_view": snap}
        else:
            return {"success": False, "error": f"未知 action: {action}"}
    except Exception as e:
        return {"success": False, "error": f"tabs 操作失败: {e}"}


# ================================================================
#  OpenAI function calling 工具 Schema
# ================================================================

EDGE_AGENT_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": (
                "获取 Edge 浏览器当前页面的可交互元素列表。"
                "返回带 @eN 引用的元素（按钮、链接、输入框等）以及 URL/Title。"
                "在每次 click/type/navigate 之后内部会自动返回新快照；"
                "当你需要主动审视页面、或页面有明显变化（如弹窗出现）时，可手动调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "导航 Edge 浏览器到指定 URL。导航完成后自动返回新的页面快照。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标 URL，必须以 http:// 或 https:// 开头"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": (
                "点击 Edge 页面上的元素。"
                "参数 ref 来自 browser_snapshot 返回的 @eN 引用（例如 @e3）。"
                "点击后自动等待页面响应并返回新快照。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "元素引用，格式为 @eN，如 @e3"},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": (
                "在 Edge 页面的输入框/可编辑区域内输入文本。"
                "参数 ref 来自 browser_snapshot 的 @eN 引用。"
                "使用 locator.fill() 触发 React/Vue 的 onChange，兼容性好。"
                "auto_submit=True 时输入完自动按 Enter 提交（搜索框/表单场景推荐）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "输入框引用，如 @e8"},
                    "text": {"type": "string", "description": "要输入的文本"},
                    "clear_first": {"type": "boolean", "description": "输入前是否先清空", "default": True},
                    "auto_submit": {"type": "boolean", "description": "输入完是否自动按 Enter 提交表单（搜索框推荐设 true）", "default": False},
                },
                "required": ["ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_press_key",
            "description": (
                "在 Edge 页面上按下键盘按键。"
                "可选值: Enter, Tab, Esc, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, "
                "Home, End, PageUp, PageDown, Backspace, Delete, F5, Ctrl+R 等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "按键名，如 Enter、Tab、ArrowDown"},
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "滚动 Edge 浏览器页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"],
                                  "description": "滚动方向", "default": "down"},
                    "amount": {"type": "integer", "description": "滚动像素数", "default": 500},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_tabs",
            "description": "管理 Edge 浏览器的标签页。list=列出; new=新开; switch=切换; close=关闭。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "new", "switch", "close"],
                               "description": "要执行的 tab 操作"},
                    "ref": {"type": "string", "description": "tab 引用（switch/close 时需要，如 @tab_1）"},
                    "url": {"type": "string", "description": "new tab 时的初始 URL（可选）"},
                },
                "required": ["action"],
            },
        },
    },
]
