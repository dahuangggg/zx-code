"""MCP client 和 router 的公共导出。

本包导出 stdio MCP client，以及把远端 MCP 工具适配成本地 ``Tool`` 实例的 router。
适配后的工具名格式为 ``mcp__<server>__<tool>``。
"""
from agent.mcp.client import MCPServerConfig, MCPToolDefinition, StdioMCPClient
from agent.mcp.router import MCPProxyTool, MCPToolRouter

__all__ = [
    "MCPProxyTool",
    "MCPServerConfig",
    "MCPToolDefinition",
    "MCPToolRouter",
    "StdioMCPClient",
]
