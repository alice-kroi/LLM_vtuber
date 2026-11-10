"""测试豆包 function calling 是否生效"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.llm_adapter import LLMAdapter


async def main():
    llm = LLMAdapter()

    # 测试 1: 单工具（参考：能成功）
    tools_simple = [
        {
            "type": "function",
            "function": {
                "name": "open_app",
                "description": "打开指定应用",
                "parameters": {
                    "type": "object",
                    "properties": {"app_name": {"type": "string"}},
                    "required": ["app_name"]
                }
            }
        }
    ]

    print(f"=== Test 1: 单工具 ===")
    print(f"Model: {llm.model}")
    result = await llm.chat(system_prompt="你是一个助手。", user_text="请打开记事本",
                              history=[], tools=tools_simple)
    print(f"Tool calls: {result.get('tool_calls')}")

    # 测试 2: 真实 71 工具
    print(f"\n=== Test 2: 71 工具 ===")
    from LLM_v2.core.persona_runtime import PersonaRuntime
    PersonaRuntime._instance = None
    runtime = PersonaRuntime()
    tools_real = runtime.assembly.active_tool_schemas()
    print(f"Real tools count: {len(tools_real)}")
    result = await llm.chat(
        system_prompt="你可以使用工具来执行操作。用户让你打开 VS Code 时必须调 os_launch_app。",
        user_text="打开 VS Code",
        history=[],
        tools=tools_real,
    )
    print(f"Tool calls: {result.get('tool_calls')}")
    print(f"Response: {result.get('response', '')[:100]}")


asyncio.run(main())