#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉分析工具模块

提供窗口截图和视觉分析功能，让 LLM 能够识别桌面/窗口画面内容。
使用 pygetwindow 截取窗口，豆包多模态模型进行分析。
"""

import asyncio
import base64
import io
import logging
import os
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# --------- 可配置参数（由 config.ini [vision] 段覆盖） ---------
_DEFAULT_ENABLED = True
_DEFAULT_MODEL = "doubao-seed-1-6-vision-250815"
_DEFAULT_API_URL = ""
_DEFAULT_API_KEY = ""
_DEFAULT_SCREENSHOT_DIR = "./screenshots"
_DEFAULT_TIMEOUT = 60  # 视觉分析超时（秒）


class VisionConfig:
    """视觉工具配置"""

    def __init__(self):
        self.enabled = _DEFAULT_ENABLED
        self.model = _DEFAULT_MODEL
        self.api_url = _DEFAULT_API_URL or os.getenv("Doubao_API_URL", "")
        self.api_key = _DEFAULT_API_KEY or os.getenv("Doubao_API_KEY", "")
        self.screenshot_dir = _DEFAULT_SCREENSHOT_DIR
        self.timeout = _DEFAULT_TIMEOUT


# 全局配置实例
vision_config = VisionConfig()


def configure_from_ini(config):
    """
    从 configparser.ConfigParser 读取 [vision] 段配置

    在 main.py 初始化时调用。
    """
    global vision_config

    if not config.has_section("vision"):
        return

    if config.has_option("vision", "enabled"):
        vision_config.enabled = config.getboolean("vision", "enabled")
    if config.has_option("vision", "model"):
        vision_config.model = config.get("vision", "model").strip()
    if config.has_option("vision", "api_url"):
        vision_config.api_url = config.get("vision", "api_url").strip() or os.getenv("Doubao_API_URL", "")
    if config.has_option("vision", "api_key"):
        vision_config.api_key = config.get("vision", "api_key").strip() or os.getenv("Doubao_API_KEY", "")
    if config.has_option("vision", "screenshot_dir"):
        vision_config.screenshot_dir = config.get("vision", "screenshot_dir").strip()
    if config.has_option("vision", "timeout"):
        vision_config.timeout = config.getint("vision", "timeout")

    logger.info(
        "[vision_tool] 配置已加载: enabled=%s, model=%s, timeout=%ds",
        vision_config.enabled, vision_config.model, vision_config.timeout
    )


def list_available_windows() -> List[Dict[str, Any]]:
    """
    列出当前系统中所有可见的窗口

    Returns:
        窗口信息列表 [{"title": str, "left": int, "top": int, "width": int, "height": int}]
    """
    try:
        import pygetwindow as gw
        windows = gw.getAllWindows()
        result = []
        for win in windows:
            if win.title:  # 过滤无标题窗口
                result.append({
                    "title": win.title,
                    "left": win.left,
                    "top": win.top,
                    "width": win.width,
                    "height": win.height,
                })
        logger.info(f"[vision_tool] 发现 {len(result)} 个可见窗口")
        return result
    except ImportError:
        logger.warning("[vision_tool] pygetwindow 未安装，无法列出窗口")
        return []
    except Exception as e:
        logger.error(f"[vision_tool] 列出窗口失败: {e}")
        return []


async def capture_window_screenshot(window_title: Optional[str] = None) -> Dict[str, Any]:
    """
    截取指定窗口或全屏截图

    Args:
        window_title: 窗口标题关键词，None 或空字符串则截取全屏

    Returns:
        {
            "success": bool,
            "image_base64": str,  # base64 编码的 PNG 图片
            "window_title": str,  # 实际截取的窗口标题
            "width": int,
            "height": int,
            "error": str  # 错误信息（如有）
        }
    """
    try:
        from PIL import Image

        if window_title and window_title.strip():
            # 截取指定窗口
            try:
                import pygetwindow as gw
                windows = gw.getWindowsWithTitle(window_title.strip())
                if not windows:
                    return {
                        "success": False,
                        "image_base64": "",
                        "window_title": "",
                        "width": 0,
                        "height": 0,
                        "error": f"未找到标题包含 '{window_title}' 的窗口"
                    }

                target_window = windows[0]
                if not target_window.visible:
                    target_window.restore()
                    await asyncio.sleep(0.3)

                # 使用 pyautogui 根据窗口坐标截图（pygetwindow 的 Win32Window 无 screenshot 方法）
                import pyautogui
                left, top = target_window.left, target_window.top
                width, height = target_window.width, target_window.height
                screenshot = pyautogui.screenshot(region=(left, top, width, height))
                actual_title = target_window.title
                width, height = screenshot.size

            except ImportError:
                return {
                    "success": False,
                    "image_base64": "",
                    "window_title": "",
                    "width": 0,
                    "height": 0,
                    "error": "pygetwindow 未安装，请执行: pip install pygetwindow pyrect"
                }
        else:
            # 截取全屏
            try:
                import pyautogui
                screenshot = pyautogui.screenshot()
                actual_title = "全屏"
                width, height = screenshot.size
            except ImportError:
                return {
                    "success": False,
                    "image_base64": "",
                    "window_title": "",
                    "width": 0,
                    "height": 0,
                    "error": "pyautogui 未安装，请执行: pip install pyautogui pillow"
                }

        # 转换为 base64
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        logger.info(f"[vision_tool] 截图成功: {actual_title} ({width}x{height})")
        return {
            "success": True,
            "image_base64": image_base64,
            "window_title": actual_title,
            "width": width,
            "height": height,
            "error": ""
        }

    except Exception as e:
        logger.error(f"[vision_tool] 截图失败: {e}")
        return {
            "success": False,
            "image_base64": "",
            "window_title": "",
            "width": 0,
            "height": 0,
            "error": f"截图失败: {e}"
        }


def _call_vision_model(image_base64: str, prompt: str) -> Dict[str, Any]:
    """
    调用豆包多模态模型分析图片（同步函数，供 asyncio.to_thread 使用）

    Args:
        image_base64: base64 编码的图片
        prompt: 分析提示词

    Returns:
        {
            "success": bool,
            "result": str,  # 模型分析结果
            "error": str  # 错误信息（如有）
        }
    """
    try:
        from openai import OpenAI

        if not vision_config.api_key:
            return {
                "success": False,
                "result": "",
                "error": "API Key 未配置，请设置 Doubao_API_KEY 环境变量或在 config.ini 中配置"
            }

        client = OpenAI(
            api_key=vision_config.api_key,
            base_url=vision_config.api_url or "https://ark.cn-beijing.volces.com/api/v3"
        )

        # 构建多模态消息
        messages = [
            {
                "role": "system",
                "content": "你是一个专业的视觉分析助手，能够详细描述图片中的内容。请用中文回答。"
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]

        response = client.chat.completions.create(
            model=vision_config.model,
            messages=messages,
            max_tokens=1024,
            timeout=vision_config.timeout
        )

        result = response.choices[0].message.content
        logger.info(f"[vision_tool] 视觉分析完成: {result[:100]}...")
        return {
            "success": True,
            "result": result,
            "error": ""
        }

    except Exception as e:
        logger.error(f"[vision_tool] 视觉分析失败: {e}")
        return {
            "success": False,
            "result": "",
            "error": f"视觉分析失败: {e}"
        }


async def vision_analyze(target: str = "desktop", prompt: str = "") -> Dict[str, Any]:
    """
    视觉分析工具：截取指定窗口/全屏并分析内容

    这是供 LLM function calling 使用的主要入口函数。

    Args:
        target: 分析目标
            - "desktop": 全屏截图
            - "window:窗口标题": 指定窗口截图（支持模糊匹配）
            - 窗口标题关键词（如"Chrome"、"记事本"）
        prompt: 分析提示词（可选，默认自动生成）

    Returns:
        {
            "success": bool,
            "target": str,  # 实际分析的目标
            "analysis": str,  # 分析结果
            "screenshot_info": {  # 截图信息
                "window_title": str,
                "width": int,
                "height": int
            },
            "error": str  # 错误信息（如有）
        }
    """
    if not vision_config.enabled:
        return {
            "success": False,
            "target": target,
            "analysis": "",
            "screenshot_info": {"window_title": "", "width": 0, "height": 0},
            "error": "视觉功能未启用，请在 config.ini [vision] 中设置 enabled = true"
        }

    # 解析 target 参数
    window_title = None
    if target.startswith("window:"):
        window_title = target[7:].strip() or None
    elif target and target != "desktop" and target != "全屏":
        # 假设是窗口标题关键词
        window_title = target.strip()

    # 执行截图
    logger.info(f"[vision_tool] 开始截图: target={target}, window_title={window_title}")
    screenshot_result = await capture_window_screenshot(window_title)

    if not screenshot_result["success"]:
        return {
            "success": False,
            "target": target,
            "analysis": "",
            "screenshot_info": {
                "window_title": screenshot_result["window_title"],
                "width": screenshot_result["width"],
                "height": screenshot_result["height"]
            },
            "error": f"截图失败: {screenshot_result['error']}"
        }

    # 构建分析提示词
    if not prompt:
        if window_title:
            prompt = f"请详细描述这个窗口的内容。这个窗口的标题包含'{window_title}'。请说出你能看到的所有元素：打开的应用、文字、图标、按钮、图片等。"
        else:
            prompt = "请详细描述这个桌面的内容。请说出你能看到的所有元素：打开的应用窗口、桌面图标、任务栏内容、背景图片等。"

    # 调用视觉模型（在线程中执行以避免阻塞事件循环）
    logger.info(f"[vision_tool] 调用视觉模型分析，提示词: {prompt[:50]}...")
    analysis_result = await asyncio.to_thread(
        _call_vision_model,
        screenshot_result["image_base64"],
        prompt
    )

    return {
        "success": analysis_result["success"],
        "target": target,
        "analysis": analysis_result["result"],
        "screenshot_info": {
            "window_title": screenshot_result["window_title"],
            "width": screenshot_result["width"],
            "height": screenshot_result["height"]
        },
        "error": analysis_result["error"]
    }


# --------- OpenAI function calling 工具 Schema ---------

VISION_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": (
                "视觉分析工具：截取指定窗口或全屏画面，并用 AI 分析画面内容。"
                "当用户询问桌面上有什么、屏幕上显示了什么、某个窗口的内容等视觉相关问题时使用此工具。"
                "注意：此工具可能耗时 3-10 秒，请耐心等待结果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "分析目标。可选值：\n"
                            "- 'desktop' 或 '全屏': 截取整个屏幕\n"
                            "- 'window:标题关键词': 截取标题包含关键词的窗口（如 'window:Chrome'）\n"
                            "- 直接写窗口标题关键词（如 'Chrome'、'记事本'）"
                        ),
                        "default": "desktop"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "分析提示词，告诉 AI 重点关注什么（可选，默认自动生成）",
                        "default": ""
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": (
                "列出当前系统中所有可见的窗口标题。"
                "当用户想知道有哪些窗口打开着，或需要查找特定窗口标题时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# 同步版本（供非异步上下文调用）
def list_windows_tool() -> Dict[str, Any]:
    """列出可见窗口（同步版本，供工具调用）"""
    if not vision_config.enabled:
        return {"success": False, "error": "视觉功能未启用"}

    windows = list_available_windows()
    return {
        "success": True,
        "windows": windows,
        "count": len(windows)
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    print("=== 视觉分析工具测试 ===")

    # 测试列出窗口
    print("\n1. 列出可见窗口:")
    windows = list_available_windows()
    for w in windows[:10]:
        print(f"   [{w['title']}] ({w['width']}x{w['height']})")
    if len(windows) > 10:
        print(f"   ... 还有 {len(windows) - 10} 个窗口")

    # 测试全屏截图
    print("\n2. 测试全屏截图:")
    result = asyncio.run(capture_window_screenshot())
    print(f"   成功: {result['success']}")
    if result['success']:
        print(f"   尺寸: {result['width']}x{result['height']}")
        print(f"   Base64 长度: {len(result['image_base64'])}")

    print("\n=== 测试完成 ===")