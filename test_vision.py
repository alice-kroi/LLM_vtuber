#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉工具独立测试脚本

分步验证：
1. 列出系统窗口
2. 全屏截图
3. 指定窗口截图
4. 视觉分析（如果 API Key 可用）
"""

import asyncio
import sys
import os
import logging
import base64

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_list_windows():
    """测试1：列出系统窗口"""
    print("\n" + "=" * 60)
    print("【测试1】列出系统可见窗口")
    print("=" * 60)

    try:
        import pygetwindow as gw
        windows = gw.getAllWindows()
        visible = [w for w in windows if w.title]
        print(f"  共发现 {len(visible)} 个可见窗口")
        for w in visible[:15]:
            print(f"  - [{w.title}] {w.width}x{w.height} @ ({w.left},{w.top})")
        if len(visible) > 15:
            print(f"  ... 还有 {len(visible) - 15} 个窗口")
        print("  ✅ 窗口列表获取成功")
        return True
    except Exception as e:
        print(f"  ❌ 窗口列表获取失败: {e}")
        return False


async def test_fullscreen_capture():
    """测试2：全屏截图"""
    print("\n" + "=" * 60)
    print("【测试2】全屏截图")
    print("=" * 60)

    try:
        from tool.vision_tool import capture_window_screenshot
        result = await capture_window_screenshot(None)

        if result["success"]:
            print(f"  ✅ 全屏截图成功: {result['width']}x{result['height']}")
            print(f"  Base64 长度: {len(result['image_base64'])} 字节")
            # 保存截图文件供检查
            img_data = base64.b64decode(result["image_base64"])
            save_path = os.path.join(os.path.dirname(__file__), "test_screenshot_fullscreen.png")
            with open(save_path, "wb") as f:
                f.write(img_data)
            print(f"  截图已保存: {save_path}")
            return True
        else:
            print(f"  ❌ 全屏截图失败: {result['error']}")
            return False
    except Exception as e:
        print(f"  ❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_window_capture(keyword=None):
    """测试3：指定窗口截图"""
    print("\n" + "=" * 60)
    print(f"【测试3】指定窗口截图 (关键词: {keyword or '无（跳过）'})")
    print("=" * 60)

    if not keyword:
        print("  ⏭️ 未指定关键词，跳过测试")
        return None

    try:
        from tool.vision_tool import capture_window_screenshot
        result = await capture_window_screenshot(keyword)

        if result["success"]:
            print(f"  ✅ 窗口截图成功: [{result['window_title']}] {result['width']}x{result['height']}")
            img_data = base64.b64decode(result["image_base64"])
            save_path = os.path.join(os.path.dirname(__file__), f"test_screenshot_{keyword}.png")
            with open(save_path, "wb") as f:
                f.write(img_data)
            print(f"  截图已保存: {save_path}")
            return True
        else:
            print(f"  ❌ 窗口截图失败: {result['error']}")
            return False
    except Exception as e:
        print(f"  ❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_vision_analysis():
    """测试4：视觉分析（需要 API Key）"""
    print("\n" + "=" * 60)
    print("【测试4】视觉分析（调用豆包多模态模型）")
    print("=" * 60)

    api_key = os.getenv("Doubao_API_KEY", "")
    if not api_key:
        print("  ⚠️  Doubao_API_KEY 未设置，跳过视觉分析测试")
        print("     请设置环境变量后重试: set Doubao_API_KEY=your-key")
        return None

    try:
        from tool.vision_tool import vision_analyze
        result = await vision_analyze(target="desktop")

        if result["success"]:
            print(f"  ✅ 视觉分析成功！")
            print(f"  目标: {result['target']}")
            print(f"  截图: {result['screenshot_info']['window_title']} ({result['screenshot_info']['width']}x{result['screenshot_info']['height']})")
            print(f"  分析结果:")
            print(f"  {'-' * 50}")
            # 打印分析结果，每行前加前缀
            for line in result['analysis'].split('\n'):
                print(f"    {line}")
            print(f"  {'-' * 50}")
            return True
        else:
            print(f"  ❌ 视觉分析失败: {result.get('error', '未知错误')}")
            return False
    except Exception as e:
        print(f"  ❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_api_key():
    """检查 API Key 配置"""
    print("\n" + "-" * 40)
    api_key = os.getenv("Doubao_API_KEY", "")
    api_url = os.getenv("Doubao_API_URL", "")
    print(f"  Doubao_API_KEY: {'✅ 已设置' if api_key else '❌ 未设置'}")
    print(f"  Doubao_API_URL: {api_url or '（使用默认值）'}")
    return bool(api_key)


async def main():
    print("=" * 60)
    print("  LLM_vtuber 视觉工具测试")
    print("=" * 60)

    # 检查环境
    has_key = check_api_key()

    results = {}

    # 测试1：列出窗口
    results["list_windows"] = test_list_windows()

    # 测试2：全屏截图
    results["fullscreen_capture"] = await test_fullscreen_capture()

    # 测试3：指定窗口截图（用第一个可见窗口的标题）
    try:
        import pygetwindow as gw
        windows = [w for w in gw.getAllWindows() if w.title]
        if windows:
            # 尝试截取一个窗口（使用标题前几个字作为关键词）
            first_title = windows[0].title[:10]
            results["window_capture"] = await test_window_capture(first_title)
        else:
            results["window_capture"] = None
    except Exception:
        results["window_capture"] = None

    # 测试4：视觉分析
    if has_key:
        results["vision_analysis"] = await test_vision_analysis()
    else:
        results["vision_analysis"] = None

    # 汇总
    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    for name, result in results.items():
        if result is True:
            print(f"  ✅ {name}: 通过")
        elif result is False:
            print(f"  ❌ {name}: 失败")
        else:
            print(f"  ⚠️  {name}: 跳过")

    passed = sum(1 for r in results.values() if r is True)
    total = len(results)
    print(f"\n  通过: {passed}/{total}")


if __name__ == "__main__":
    asyncio.run(main())