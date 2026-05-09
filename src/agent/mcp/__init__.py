"""agent.mcp — MCP（Model Context Protocol）协议客户端（s19）。

使用 Anthropic 官方 ``mcp`` Python SDK，通过 stdio 传输层连接外部 MCP 服务器。

  client.py — ``StdioMCPClient``：懒初始化的 stdio MCP 客户端，list_tools / call_tool
  router.py — ``MCPToolRouter``：管理多个 MCP 服务器，将其工具注册为统一的 ``MCPProxyTool``

工具命名约定：``mcp__<server_name>__<tool_name>``
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
