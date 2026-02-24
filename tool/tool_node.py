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


# 注册示例工具
tool_registry.register_tool("example_tool", example_tool)
tool_registry.register_tool("add_numbers", add_numbers)


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
            }
        ]
    }
    
    result = tool_dispatch_node(test_state)
    print(f"工具调用结果: {result['tool_results']}")
    print(f"错误: {result['error']}")
    
    print("\n=== 测试完成 ===")
