#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SkillWorld MCP 适配层（扩展能力）：把激活域的工具暴露为标准 MCP Server。

任意 MCP 客户端（Claude Desktop、Cline、Codex 等）可经 stdio 连接，
直接调用 LLM_vtuber 的 cu_/br_/uip_ 等域工具（子进程引擎照常工作，懒启动）。

依赖：mcp（LLM env 已装）。缺失时模块导入不报错，仅能力不可用。

用法：
    python -m LLM.skill_world_mcp                      # 启动 stdio MCP server
    客户端配置:
      "mcpServers": {"llm-vtuber": {"command": "C:/ProgramData/anaconda3/envs/LLM/python.exe",
                                    "args": ["-m", "LLM.skill_world_mcp"]}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)

MCP_AVAILABLE = False
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as mtypes
    MCP_AVAILABLE = True
except Exception:  # pragma: no cover
    Server = mtypes = None


def build_mcp_server(assembly=None, server_name: str = "llm-vtuber"):
    """构建 MCP Server（request-handler API）：list_tools 用装配层激活 schema，
    call_tool 转发 tool_registry.execute_tool（代理工具 → 子进程引擎），返回 JSON 文本。
    """
    if not MCP_AVAILABLE:
        raise RuntimeError("缺 mcp 依赖：pip install mcp")
    from tool.tool_node import ToolCall, tool_registry
    if assembly is None:
        from LLM.skill_world import skill_assembly
        assembly = skill_assembly

    server = Server(server_name)

    async def _handle_list_tools(ctx, req: mtypes.ListToolsRequest) -> mtypes.ListToolsResult:
        tools = []
        for s in assembly.active_tool_schemas():
            fn = s.get("function", {})
            tools.append(mtypes.Tool(
                name=fn.get("name", ""),
                description=fn.get("description", ""),
                inputSchema=fn.get("parameters",
                                   {"type": "object", "properties": {}}),
            ))
        return mtypes.ListToolsResult(tools=tools)

    async def _handle_call_tool(ctx, params: mtypes.CallToolRequestParams) -> mtypes.CallToolResult:
        name = params.name
        args = params.arguments or {}
        tc = ToolCall(tool_call_id=f"mcp_{name}", name=name, arguments=args)
        r = await tool_registry.execute_tool(tc, timeout=300.0)
        text = json.dumps(r if isinstance(r, dict) else {"result": r},
                          ensure_ascii=False)
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=text)],
            isError=False)

    server.add_request_handler("tools/list", mtypes.ListToolsRequest, _handle_list_tools)
    server.add_request_handler("tools/call", mtypes.CallToolRequestParams, _handle_call_tool)
    # 暴露给嵌入方/测试直接调用
    server._handlers = {"list_tools": _handle_list_tools, "call_tool": _handle_call_tool}
    return server


async def _run_stdio(server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def main() -> int:
    if not MCP_AVAILABLE:
        print("缺 mcp 依赖：pip install mcp", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.WARNING)
    # 启动装配层（自动发现+激活域），确保代理工具注册进 tool_registry（懒启动：首次调用拉起子进程）
    from LLM.skill_world import skill_assembly
    asyncio.run(skill_assembly.start_all())
    server = build_mcp_server(skill_assembly)
    asyncio.run(_run_stdio(server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
