#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edge 浏览器信息获取工具

使用 Playwright async API 驱动 Edge/Chromium 浏览器，
提供 web_search 和 fetch_webpage 两个工具函数供 LLM 调用。
"""

import asyncio
import logging
import os
from urllib.parse import quote_plus, urlparse
from typing import Optional, Dict, Any, List

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Error as PlaywrightError,
)

logger = logging.getLogger(__name__)

# --------- 可配置参数（由 config.ini [browser] 段覆盖） ---------
_DEFAULT_HEADLESS = True
_DEFAULT_TIMEOUT = 30_000          # 页面操作超时（毫秒）
_DEFAULT_MAX_CONTENT = 5000        # 抓取内容最大字符数
_DEFAULT_TOOL_TIMEOUT = 60         # 单次工具调用超时（秒）
_DEFAULT_SEARCH_ENGINE = "bing"    # 搜索引擎

_SEARCH_URLS = {
    "bing": "https://www.bing.com/search?q={query}",
    "baidu": "https://www.baidu.com/s?wd={query}",
}

# 搜索结果选择器（各引擎不同）
_SEARCH_SELECTORS = {
    "bing": {
        "result_block": "#b_results .b_algo",
        "title": "h2",
        "link": "h2 a",
        "snippet": ".b_caption p",
    },
    "baidu": {
        "result_block": ".result.c-container",
        "title": "h3",
        "link": "h3 a",
        "snippet": ".c-abstract",
    },
}


class BrowserManager:
    """
    浏览器连接管理器（单例，复用浏览器实例）

    - 应用启动时创建一次 Browser 实例
    - 每次工具调用创建独立 BrowserContext（隔离 Cookie/缓存）
    - 调用结束后关闭 Context，保留 Browser
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._headless = _DEFAULT_HEADLESS

    async def start(self, headless: bool = None):
        """启动浏览器（应用初始化时调用一次）"""
        async with self._lock:
            if headless is not None:
                self._headless = headless
            if self._browser and self._browser.is_connected():
                return
            self._playwright = await async_playwright().start()
            # 优先尝试系统安装的 Edge；失败则回退到 Playwright 自带 Chromium
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    channel="msedge",
                    args=["--disable-gpu", "--no-sandbox"],
                )
                logger.info("Edge 浏览器已启动 (channel=msedge, headless=%s)", self._headless)
            except PlaywrightError as e:
                logger.warning("启动 Edge 失败(%s)，回退到 Chromium", e)
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=["--disable-gpu", "--no-sandbox"],
                )
                logger.info("Chromium 浏览器已启动 (headless=%s)", self._headless)

    async def stop(self):
        """关闭浏览器（应用退出时调用）"""
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            logger.info("浏览器已关闭")

    async def new_context(self) -> BrowserContext:
        """创建新的浏览器上下文（每次工具调用独立隔离）"""
        if not self._browser or not self._browser.is_connected():
            await self.start()
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        context.set_default_timeout(_DEFAULT_TIMEOUT)
        return context


# 全局单例
browser_manager = BrowserManager()


# --------- 安全校验 ---------

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172.16."}


def _validate_url(url: str) -> bool:
    """URL 安全校验：仅允许 http/https 协议，屏蔽内网地址"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        for blocked in _BLOCKED_HOSTS:
            if host == blocked or host.startswith(blocked):
                return False
        return True
    except Exception:
        return False


# --------- 工具函数 ---------

async def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    使用浏览器进行网页搜索，返回搜索结果列表。

    Args:
        query: 搜索关键词
        max_results: 最大返回结果数（默认 5）

    Returns:
        {"success": True, "results": [{"title", "url", "snippet"}, ...]}
    """
    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    engine = _DEFAULT_SEARCH_ENGINE
    url_template = _SEARCH_URLS.get(engine, _SEARCH_URLS["bing"])
    selectors = _SEARCH_SELECTORS.get(engine, _SEARCH_SELECTORS["bing"])
    search_url = url_template.format(query=quote_plus(query.strip()))

    logger.info("[browser_tool] web_search: query=%r, engine=%s", query, engine)

    context = None
    try:
        context = await browser_manager.new_context()
        page: Page = await context.new_page()

        await page.goto(search_url, wait_until="domcontentloaded")
        # 等待搜索结果容器出现
        try:
            await page.wait_for_selector(selectors["result_block"], timeout=10_000)
        except PlaywrightError:
            logger.warning("[browser_tool] 搜索结果选择器未找到，尝试继续解析")

        # 解析搜索结果
        results: List[Dict[str, str]] = []
        blocks = await page.query_selector_all(selectors["result_block"])
        for block in blocks:
            if len(results) >= max_results:
                break
            try:
                title_el = await block.query_selector(selectors["title"])
                link_el = await block.query_selector(selectors["link"])
                snippet_el = await block.query_selector(selectors["snippet"])

                title = await title_el.inner_text() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                snippet = await snippet_el.inner_text() if snippet_el else ""

                if title and href:
                    results.append({
                        "title": title.strip(),
                        "url": href.strip(),
                        "snippet": snippet.strip()[:300],
                    })
            except Exception:
                continue

        logger.info("[browser_tool] web_search 完成，获取 %d 条结果", len(results))
        return {"success": True, "results": results}

    except PlaywrightError as e:
        error_msg = f"搜索失败: {e}"
        logger.error(f"[browser_tool] {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = f"搜索异常: {e}"
        logger.error(f"[browser_tool] {error_msg}")
        return {"success": False, "error": error_msg}
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def fetch_webpage(url: str, extract_mode: str = "text",
                        max_length: int = None) -> Dict[str, Any]:
    """
    抓取指定 URL 的网页内容。

    Args:
        url: 目标网页 URL
        extract_mode: 提取模式 — "text"(纯文本) | "article"(正文主体) | "html"(原始HTML)
        max_length: 返回内容最大字符数

    Returns:
        {"success": True, "title": str, "content": str, "url": str}
    """
    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}

    url = url.strip()
    if not _validate_url(url):
        return {"success": False, "error": f"URL 不安全或协议不支持: {url}"}

    if max_length is None:
        max_length = _DEFAULT_MAX_CONTENT

    logger.info("[browser_tool] fetch_webpage: url=%r, mode=%s", url, extract_mode)

    context = None
    try:
        context = await browser_manager.new_context()
        page: Page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=_DEFAULT_TIMEOUT)
        # 等待网络空闲（最多等 5 秒）
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except PlaywrightError:
            pass  # 超时不阻塞，继续提取内容

        title = await page.title()

        # 根据模式提取内容
        if extract_mode == "html":
            content = await page.evaluate("() => document.body.innerHTML")
        elif extract_mode == "article":
            # 优先提取 article/main 标签，回退到 body
            content = await page.evaluate("""
                () => {
                    const article = document.querySelector('article')
                        || document.querySelector('main')
                        || document.querySelector('.article-content')
                        || document.querySelector('.post-content');
                    if (article) return article.innerText;
                    return document.body.innerText;
                }
            """)
        else:  # text
            content = await page.evaluate("() => document.body.innerText")

        # 截断到最大长度
        if content and len(content) > max_length:
            content = content[:max_length] + "\n...(内容已截断)"

        final_url = page.url  # 可能经过重定向

        logger.info("[browser_tool] fetch_webpage 完成，标题=%r，内容长度=%d",
                     title, len(content or ""))
        return {
            "success": True,
            "title": title,
            "content": content or "",
            "url": final_url,
        }

    except PlaywrightError as e:
        error_msg = f"页面抓取失败: {e}"
        logger.error(f"[browser_tool] {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        error_msg = f"页面抓取异常: {e}"
        logger.error(f"[browser_tool] {error_msg}")
        return {"success": False, "error": error_msg}
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass


# --------- OpenAI function calling 工具 Schema ---------

BROWSER_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "使用搜索引擎搜索网页信息，返回标题、链接和摘要。"
                "适用于需要查询最新信息、新闻、天气、技术文档等场景。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认5）",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": (
                "抓取指定URL的网页正文内容。"
                "适用于需要获取具体网页详细信息的场景。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页URL"},
                    "extract_mode": {
                        "type": "string",
                        "enum": ["text", "article", "html"],
                        "description": "提取模式：text=纯文本, article=正文主体, html=原始HTML",
                        "default": "text",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "返回内容最大字符数（默认5000）",
                        "default": 5000,
                    },
                },
                "required": ["url"],
            },
        },
    },
]


def configure_from_ini(config):
    """
    从 configparser.ConfigParser 读取 [browser] 段配置，
    覆盖模块级默认值。

    在 main.py 初始化时调用。
    """
    global _DEFAULT_HEADLESS, _DEFAULT_TIMEOUT, _DEFAULT_MAX_CONTENT
    global _DEFAULT_TOOL_TIMEOUT, _DEFAULT_SEARCH_ENGINE

    if not config.has_section("browser"):
        return

    if config.has_option("browser", "headless"):
        _DEFAULT_HEADLESS = config.getboolean("browser", "headless")
    if config.has_option("browser", "timeout"):
        _DEFAULT_TIMEOUT = config.getint("browser", "timeout") * 1000
    if config.has_option("browser", "max_content_length"):
        _DEFAULT_MAX_CONTENT = config.getint("browser", "max_content_length")
    if config.has_option("browser", "tool_timeout"):
        _DEFAULT_TOOL_TIMEOUT = config.getint("browser", "tool_timeout")
    if config.has_option("browser", "search_engine"):
        engine = config.get("browser", "search_engine").strip().lower()
        if engine in _SEARCH_URLS:
            _DEFAULT_SEARCH_ENGINE = engine

    logger.info(
        "[browser_tool] 配置已加载: headless=%s, timeout=%dms, max_content=%d, "
        "tool_timeout=%ds, engine=%s",
        _DEFAULT_HEADLESS, _DEFAULT_TIMEOUT, _DEFAULT_MAX_CONTENT,
        _DEFAULT_TOOL_TIMEOUT, _DEFAULT_SEARCH_ENGINE,
    )
