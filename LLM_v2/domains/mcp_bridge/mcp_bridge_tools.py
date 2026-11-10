# ============================================================
# LLM_v2/domains/mcp_bridge/mcp_bridge_tools.py — MCP 桥接工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

MCP_BRIDGE_MANAGEMENT_TOOLS = [
    "mcp_status", "mcp_refresh", "mcp_servers", "mcp_call",
]

MCP_BRIDGE_TOOLS_SCHEMA: list = [
    tool_schema(
        "mcp_status",
        "查看 MCP 桥接状态：已配置服务器、连接状态、远程工具数量与错误。",
        {},
    ),
    tool_schema(
        "mcp_refresh",
        "重新连接所有 MCP 服务器并刷新远程工具列表（新增/删除服务器后使用）。",
        {},
    ),
    tool_schema(
        "mcp_servers",
        "列出 config/mcp_servers.json 中配置的 MCP 服务器（名称/传输/前缀/启用）。",
        {},
    ),
    tool_schema(
        "mcp_call",
        "通用 MCP 调用：指定 server、tool、arguments。远程工具未自动注册时可用。",
        {
            "server": {"type": "string", "description": "服务器名称（mcp_servers 返回的 name）"},
            "tool": {"type": "string", "description": "远程工具原名"},
            "arguments": {"type": "object", "description": "远程工具参数"},
        },
        required=["server", "tool"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in MCP_BRIDGE_TOOLS_SCHEMA
            if s["function"]["name"] in names]
