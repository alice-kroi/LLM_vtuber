#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器工具单元测试

测试 web_search 和 fetch_webpage 功能。
运行: python -m tool.test_browser_tool
"""

import asyncio
import logging
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_web_search():
    """测试 web_search"""
    from tool.browser_tool import web_search, browser_manager

    print("\n=== 测试 web_search ===")

    # 启动浏览器
    await browser_manager.start(headless=True)

    try:
        # 测试 1: 搜索中文关键词
        print("\n--- 测试 1: 搜索 'Python asyncio' ---")
        result = await web_search("Python asyncio 教程", max_results=3)
        print(f"success: {result.get('success')}")
        if result.get("success"):
            print(f"结果数: {len(result['results'])}")
            for i, r in enumerate(result["results"]):
                print(f"  [{i+1}] {r['title']}")
                print(f"      URL: {r['url']}")
                print(f"      摘要: {r['snippet'][:80]}...")
        else:
            print(f"错误: {result.get('error')}")

        # 测试 2: 空关键词
        print("\n--- 测试 2: 空关键词 ---")
        result = await web_search("", max_results=3)
        print(f"success: {result.get('success')} (应为 False)")
        print(f"error: {result.get('error')}")

        # 测试 3: 搜索英文关键词
        print("\n--- 测试 3: 搜索 'latest tech news' ---")
        result = await web_search("latest tech news", max_results=2)
        print(f"success: {result.get('success')}")
        if result.get("success"):
            print(f"结果数: {len(result['results'])}")
            for i, r in enumerate(result["results"]):
                print(f"  [{i+1}] {r['title']}")

    finally:
        await browser_manager.stop()


async def test_fetch_webpage():
    """测试 fetch_webpage"""
    from tool.browser_tool import fetch_webpage, browser_manager

    print("\n=== 测试 fetch_webpage ===")

    await browser_manager.start(headless=True)

    try:
        # 测试 1: 抓取 example.com (text 模式)
        print("\n--- 测试 1: fetch 'https://example.com' (text) ---")
        result = await fetch_webpage("https://example.com", extract_mode="text", max_length=2000)
        print(f"success: {result.get('success')}")
        if result.get("success"):
            print(f"标题: {result['title']}")
            print(f"URL: {result['url']}")
            print(f"内容长度: {len(result['content'])}")
            print(f"内容前200字: {result['content'][:200]}")
        else:
            print(f"错误: {result.get('error')}")

        # 测试 2: 空 URL
        print("\n--- 测试 2: 空 URL ---")
        result = await fetch_webpage("", extract_mode="text")
        print(f"success: {result.get('success')} (应为 False)")

        # 测试 3: 内网地址（应被拒绝）
        print("\n--- 测试 3: 内网地址 ---")
        result = await fetch_webpage("http://127.0.0.1:8080", extract_mode="text")
        print(f"success: {result.get('success')} (应为 False)")
        print(f"error: {result.get('error')}")

        # 测试 4: article 模式
        print("\n--- 测试 4: fetch 'https://example.com' (article) ---")
        result = await fetch_webpage("https://example.com", extract_mode="article", max_length=1000)
        print(f"success: {result.get('success')}")
        if result.get("success"):
            print(f"内容长度: {len(result['content'])}")

    finally:
        await browser_manager.stop()


async def test_tool_registry():
    """测试通过 ToolRegistry 执行浏览器工具"""
    from tool.tool_node import tool_registry

    print("\n=== 测试 ToolRegistry 执行浏览器工具 ===")

    # 检查工具是否已注册
    print(f"已注册工具: {list(tool_registry.tools.keys())}")

    # 通过 registry 执行 web_search
    tool_call = {
        "tool_call_id": "test-1",
        "name": "web_search",
        "arguments": {"query": "今天天气", "max_results": 2}
    }
    result = await tool_registry.execute_tool(tool_call, timeout=60.0)
    print(f"web_search 结果: success={result.get('result', {}).get('success', False) if result.get('result') else False}")
    if result.get("error"):
        print(f"  error: {result['error']}")

    # 通过 registry 执行 fetch_webpage
    tool_call = {
        "tool_call_id": "test-2",
        "name": "fetch_webpage",
        "arguments": {"url": "https://example.com", "max_length": 500}
    }
    result = await tool_registry.execute_tool(tool_call, timeout=60.0)
    print(f"fetch_webpage 结果: success={result.get('result', {}).get('success', False) if result.get('result') else False}")
    if result.get("error"):
        print(f"  error: {result['error']}")


async def main():
    """运行所有测试"""
    print("╔══════════════════════════════════════╗")
    print("║   浏览器工具单元测试                 ║")
    print("╚══════════════════════════════════════╝")

    await test_web_search()
    await test_fetch_webpage()
    await test_tool_registry()

    print("\n╔══════════════════════════════════════╗")
    print("║   所有测试完成                       ║")
    print("╚══════════════════════════════════════╝")


if __name__ == "__main__":
    asyncio.run(main())
