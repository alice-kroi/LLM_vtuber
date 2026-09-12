#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具节点模块

实现工具调度节点和工具注册表，
为 LLM_vtuber 项目提供工具调用能力。
"""

from typing_extensions import TypedDict
from typing import Optional, Dict, List, Union, Callable, Any
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class ToolCall(TypedDict):
    """
    工具调用定义
    
    字段说明：
    - tool_call_id: 工具调用ID
    - name: 工具名称
    - arguments: 工具参数
    """
    tool_call_id: str
    name: str
    arguments: Dict[str, Any]


class ToolResult(TypedDict):
    """
    工具结果定义
    
    字段说明：
    - tool_call_id: 工具调用ID
    - tool_name: 工具名称
    - result: 工具执行结果
    - error: 错误信息（如有）
    """
    tool_call_id: str
    tool_name: str
    result: Optional[Any] = None
    error: Optional[str] = None


class ToolRegistry:
    """
    工具注册表
    
    管理可用的工具函数，提供工具查找和执行功能。
    """
    
    def __init__(self):
        """
        初始化工具注册表
        """
        self.tools: Dict[str, Callable] = {}
    
    def register_tool(self, name: str, tool_func: Callable):
        """
        注册工具函数
        
        Args:
            name: 工具名称
            tool_func: 工具函数
        """
        self.tools[name] = tool_func
        logger.info(f"已注册工具: {name}")
    
    def get_tool(self, name: str) -> Optional[Callable]:
        """
        获取工具函数
        
        Args:
            name: 工具名称
        
        Returns:
            工具函数，如果不存在则返回 None
        """
        return self.tools.get(name)
    
    async def execute_tool(self, tool_call: ToolCall, timeout: float = 60.0) -> ToolResult:
        """
        执行工具调用（支持同步和异步工具函数）

        Args:
            tool_call: 工具调用请求
            timeout: 工具执行超时时间（秒）

        Returns:
            工具执行结果
        """
        try:
            tool_name = tool_call["name"]
            tool_func = self.get_tool(tool_name)

            if not tool_func:
                return ToolResult(
                    tool_call_id=tool_call["tool_call_id"],
                    tool_name=tool_name,
                    error=f"工具不存在: {tool_name}"
                )

            # 异步工具：await + 超时保护
            if asyncio.iscoroutinefunction(tool_func):
                result = await asyncio.wait_for(
                    tool_func(**tool_call["arguments"]),
                    timeout=timeout
                )
            else:
                # 同步工具：直接调用
                result = tool_func(**tool_call["arguments"])

            return ToolResult(
                tool_call_id=tool_call["tool_call_id"],
                tool_name=tool_name,
                result=result
            )

        except asyncio.TimeoutError:
            error_msg = f"工具执行超时 ({timeout}s): {tool_call.get('name', 'unknown')}"
            logger.error(error_msg)
            return ToolResult(
                tool_call_id=tool_call.get("tool_call_id", ""),
                tool_name=tool_call.get("name", "unknown"),
                error=error_msg
            )
        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            logger.error(error_msg)
            return ToolResult(
                tool_call_id=tool_call.get("tool_call_id", ""),
                tool_name=tool_call.get("name", "unknown"),
                error=error_msg
            )


# 全局工具注册表实例
tool_registry = ToolRegistry()


async def tool_dispatch_node(state: Dict) -> Dict:
    """
    工具调度节点（异步）

    接收工具调用请求，解析工具参数，执行相应工具并返回结果。
    支持同步和异步工具函数。

    Args:
        state: 状态对象，包含工具调用请求

    Returns:
        更新后的状态，包含工具执行结果
    """
    try:
        logger.info("执行工具调度节点")

        # 获取工具调用请求
        tool_calls = state.get("tool_calls", [])

        if not tool_calls:
            logger.warning("未收到工具调用请求")
            return {
                **state,
                "tool_results": [],
                "error": None
            }

        # 执行所有工具调用
        tool_results = []
        for tool_call in tool_calls:
            result = await tool_registry.execute_tool(tool_call)
            tool_results.append(result)

        logger.info(f"工具执行完成，共处理 {len(tool_results)} 个工具调用")

        # 更新状态并返回
        return {
            **state,
            "tool_results": tool_results,
            "error": None
        }

    except Exception as e:
        error_msg = f"工具调度节点失败: {str(e)}"
        logger.error(error_msg)
        return {
            **state,
            "tool_results": [],
            "error": error_msg
        }


# 示例工具函数
def example_tool(text: str, repeat: int = 2) -> str:
    """
    示例工具函数
    
    将输入文本重复指定次数。
    
    Args:
        text: 输入文本
        repeat: 重复次数
    
    Returns:
        重复后的文本
    """
    return text * repeat


def add_numbers(a: int, b: int) -> int:
    """
    加法工具函数
    
    计算两个数的和。
    
    Args:
        a: 第一个数
        b: 第二个数
    
    Returns:
        两个数的和
    """
    return a + b


# 导入音频处理模块
import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from audio.audio_deal import play_audio


def play_audio_tool(file_path: str, duration: float = None) -> Dict[str, Any]:
    """
    播放音频工具函数

    从指定路径播放音频文件，可选择播放时长

    Args:
        file_path: 音频文件的路径
        duration: 播放时长（秒），None表示播放完整音频

    Returns:
        Dict[str, Any]: 包含播放结果的字典
            - success: bool, 播放是否成功
            - played_duration: float, 实际播放时长（秒）
            - total_duration: float, 音频总时长（秒）
            - error: str, 错误信息（如果有）
    """
    try:
        # 验证文件路径
        if not file_path:
            return {
                "success": False,
                "error": "音频文件路径不能为空"
            }
        
        # 验证文件是否存在
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"音频文件不存在: {file_path}"
            }
        
        # 验证文件是否为音频文件
        valid_extensions = [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".wma"]
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in valid_extensions:
            return {
                "success": False,
                "error": f"不支持的音频文件格式: {file_ext}"
            }
        
        # 调用音频播放函数
        result = play_audio(file_path, duration)
        return result
        
    except Exception as e:
        error_msg = f"播放音频失败: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg
        }


def calculator_tool(expression: str) -> Dict[str, Any]:
    """
    计算器工具函数

    执行基本算术运算（加、减、乘、除），支持数学表达式字符串

    Args:
        expression: 数学表达式字符串，例如 "2 + 3 * 4"

    Returns:
        Dict[str, Any]: 包含计算结果的字典
            - success: bool, 计算是否成功
            - result: float, 计算结果
            - error: str, 错误信息（如果有）
    """
    try:
        # 验证表达式
        if not expression:
            return {
                "success": False,
                "error": "数学表达式不能为空"
            }
        
        # 安全执行表达式
        # 注意：使用 eval 存在安全风险，仅用于内部工具
        # 这里添加基本的安全检查
        allowed_chars = "0123456789 +-*/(). "
        for char in expression:
            if char not in allowed_chars:
                return {
                    "success": False,
                    "error": f"表达式包含不允许的字符: {char}"
                }
        
        # 执行计算
        result = eval(expression)
        
        return {
            "success": True,
            "result": result
        }
        
    except ZeroDivisionError:
        error_msg = "除数不能为零"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg
        }
    except SyntaxError:
        error_msg = "数学表达式语法错误"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg
        }
    except Exception as e:
        error_msg = f"计算失败: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg
        }


# ============ TTS 工具（S3：工具化 TTS，混合模式兜底） ============

async def tts_synthesize(
    text: str,
    tone: str = "",
    mode: str = "cloud",
) -> Dict[str, Any]:
    """
    合成并播放语音（工具版 TTS）

    当 LLM 需要主动控制说话时机时调用此工具（如朗读搜索结果、播报长文）。
    默认使用云端 TTS（更稳定），可在流式环境自动降级本地。

    Args:
        text: 要合成的文本（内部会自动清理表情/特殊字符）
        tone: 语气标签（影响本地参考音频选择，如 开心/撒娇/调皮）
        mode: "cloud"（云端，默认）或 "local"（本地 GPT-SoVITS）

    Returns:
        {"success": bool, "duration": float, "error": str}
    """
    from audio.audio_main import (
        cloud_tts_request_and_play,
        tts_request_and_stream_play,
        clean_text_for_tts,
        get_cloud_tts_config,
    )

    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return {"success": False, "error": "文本清洗后为空"}

    effective_mode = mode
    if mode == "cloud":
        cfg = get_cloud_tts_config()
        if cfg is None or not getattr(cfg, "enabled", False):
            logger.warning("[TTS工具] 云端 TTS 未启用，自动降级本地")
            effective_mode = "local"

    if effective_mode == "cloud":
        try:
            result = await cloud_tts_request_and_play(cleaned, get_cloud_tts_config())
            return {
                "success": result.get("success", False),
                "duration": result.get("audio_duration", 0.0),
                "error": result.get("error"),
            }
        except Exception as e:
            logger.error(f"[TTS工具] 云端合成失败，降级本地: {e}")
            effective_mode = "local"

    # 本地 TTS
    try:
        ref_audio = ""
        if tone:
            from audio.audio_main import find_ref_audio_by_tone
            ref_audio = find_ref_audio_by_tone(tone)
        result = await tts_request_and_stream_play(cleaned, "", ref_audio)
        return {
            "success": result.get("success", False),
            "duration": result.get("audio_duration", 0.0),
            "error": result.get("error"),
        }
    except Exception as e:
        return {"success": False, "error": f"TTS工具执行失败: {e}"}


# 注册示例工具
tool_registry.register_tool("tts_synthesize", tts_synthesize)
tool_registry.register_tool("example_tool", example_tool)
tool_registry.register_tool("add_numbers", add_numbers)
tool_registry.register_tool("play_audio", play_audio_tool)
tool_registry.register_tool("calculator", calculator_tool)

# 注册浏览器工具
try:
    from tool.browser_tool import web_search, fetch_webpage, browser_manager
    tool_registry.register_tool("web_search", web_search)
    tool_registry.register_tool("fetch_webpage", fetch_webpage)
    logger.info("浏览器工具已注册: web_search, fetch_webpage")
except ImportError as e:
    logger.warning(f"浏览器工具未注册（playwright 未安装）: {e}")

# 注册视觉分析工具
try:
    from tool.vision_tool import vision_analyze, list_windows_tool
    tool_registry.register_tool("vision_analyze", vision_analyze)
    tool_registry.register_tool("list_windows", list_windows_tool)
    logger.info("视觉分析工具已注册: vision_analyze, list_windows")
except ImportError as e:
    logger.warning(f"视觉分析工具未注册（依赖未安装）: {e}")

# 注册 Edge 交互浏览器工具（EdgeAgent —— 像 TRAE 浏览器 agent 一样操控 Edge）
try:
    from tool.edge_agent import (
        browser_snapshot, browser_navigate, browser_click, browser_type,
        browser_press_key, browser_scroll, browser_tabs,
    )
    tool_registry.register_tool("browser_snapshot", browser_snapshot)
    tool_registry.register_tool("browser_navigate", browser_navigate)
    tool_registry.register_tool("browser_click", browser_click)
    tool_registry.register_tool("browser_type", browser_type)
    tool_registry.register_tool("browser_press_key", browser_press_key)
    tool_registry.register_tool("browser_scroll", browser_scroll)
    tool_registry.register_tool("browser_tabs", browser_tabs)
    logger.info("Edge 交互浏览器工具已注册: browser_snapshot/navigate/click/type/press_key/scroll/tabs")
except ImportError as e:
    logger.warning(f"Edge 交互浏览器工具未注册（依赖未安装）: {e}")

# 注册 AI 操作电脑（Computer Use）工具（键盘/鼠标/窗口/视觉定位）
try:
    from tool.computer_use import (
        os_list_windows, os_activate_window, os_click_xy, os_type_text,
        os_press_key, os_scroll, ui_list_elements, vision_click,
        os_open_path, os_list_directory, os_minimize_window, os_show_desktop,
    )
    tool_registry.register_tool("os_list_windows", os_list_windows)
    tool_registry.register_tool("os_activate_window", os_activate_window)
    tool_registry.register_tool("os_click_xy", os_click_xy)
    tool_registry.register_tool("os_type_text", os_type_text)
    tool_registry.register_tool("os_press_key", os_press_key)
    tool_registry.register_tool("os_scroll", os_scroll)
    tool_registry.register_tool("ui_list_elements", ui_list_elements)
    tool_registry.register_tool("vision_click", vision_click)
    tool_registry.register_tool("os_open_path", os_open_path)
    tool_registry.register_tool("os_list_directory", os_list_directory)
    tool_registry.register_tool("os_minimize_window", os_minimize_window)
    tool_registry.register_tool("os_show_desktop", os_show_desktop)
    logger.info("AI 操作电脑工具已注册: os_list_windows/activate_window/click_xy/type_text/press_key/scroll/ui_list_elements/vision_click/open_path/list_directory/minimize_window/show_desktop")
except ImportError as e:
    logger.warning(f"AI 操作电脑工具未注册（依赖未安装）: {e}")


if __name__ == "__main__":
    """
    工具节点测试主函数
    """
    # 配置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    print("=== 测试工具节点 ===")
    
    # 测试工具调度节点
    test_state = {
        "tool_calls": [
            {
                "tool_call_id": "1",
                "name": "add_numbers",
                "arguments": {"a": 5, "b": 3}
            },
            {
                "tool_call_id": "2",
                "name": "example_tool",
                "arguments": {"text": "Hello", "repeat": 3}
            },
            {
                "tool_call_id": "3",
                "name": "calculator",
                "arguments": {"expression": "2 + 3 * 4"}
            }
        ]
    }
    
    result = tool_dispatch_node(test_state)
    print(f"工具调用结果: {result['tool_results']}")
    print(f"错误: {result['error']}")
    
    # 测试计算器工具
    print("\n=== 测试计算器工具 ===")
    calculator_test_cases = [
        "2 + 3",
        "5 * 4",
        "10 - 3",
        "8 / 2",
        "(2 + 3) * 4",
        "10 / 0"  # 测试除零错误
    ]
    
    for test_case in calculator_test_cases:
        result = calculator_tool(test_case)
        print(f"表达式: {test_case}")
        print(f"结果: {result}")
    
    # 测试音频播放工具
    print("\n=== 测试音频播放工具 ===")
    # 注意：这里需要提供一个实际的音频文件路径进行测试
    # 请将下面的路径替换为实际的音频文件路径
    test_audio_path = "example_audio.mp3"
    if os.path.exists(test_audio_path):
        result = play_audio_tool(test_audio_path, duration=2)
        print(f"音频播放结果: {result}")
    else:
        print(f"音频文件不存在: {test_audio_path}")
        print("请提供一个实际的音频文件路径进行测试")
    
    print("\n=== 测试完成 ===")
