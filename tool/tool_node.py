#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具节点模块

实现工具调度节点和工具注册表，
为 LLM_vtuber 项目提供工具调用能力。
"""

from typing_extensions import TypedDict
from typing import Optional, Dict, List, Union, Callable, Any
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
    
    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """
        执行工具调用
        
        Args:
            tool_call: 工具调用请求
        
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
            
            # 执行工具
            result = tool_func(**tool_call["arguments"])
            
            return ToolResult(
                tool_call_id=tool_call["tool_call_id"],
                tool_name=tool_name,
                result=result
            )
            
        except Exception as e:
            error_msg = f"工具执行失败: {str(e)}"
            logger.error(error_msg)
            return ToolResult(
                tool_call_id=tool_call["tool_call_id"],
                tool_name=tool_call.get("name", "unknown"),
                error=error_msg
            )


# 全局工具注册表实例
tool_registry = ToolRegistry()


def tool_dispatch_node(state: Dict) -> Dict:
    """
    工具调度节点
    
    接收工具调用请求，解析工具参数，执行相应工具并返回结果。
    
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
            result = tool_registry.execute_tool(tool_call)
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


# 注册示例工具
tool_registry.register_tool("example_tool", example_tool)
tool_registry.register_tool("add_numbers", add_numbers)
tool_registry.register_tool("play_audio", play_audio_tool)
tool_registry.register_tool("calculator", calculator_tool)


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
