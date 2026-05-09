"""mcp.client — MCP stdio 客户端（官方 SDK 封装）。

``StdioMCPClient`` 通过 ``mcp`` 官方 Python SDK 连接本地 MCP 服务器进程：
  - 懒初始化：首次调用 list_tools() 或 call_tool() 时才启动进程
  - 使用 AsyncExitStack 管理生命周期，close() 负责清理进程和 session
  - list_tools() 返回工具定义列表（MCPToolDefinition）
  - call_tool() 返回工具执行结果（文本或结构化 dict）

MCP 协议握手流程（由官方 SDK 处理）：
  initialize → notifications/initialized → tools/list / tools/call
"""
from __future__ import annotations

import contextlib
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class StdioMCPClient:
    """MCP client backed by the official Anthropic mcp SDK (stdio transport)."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._session: Any | None = None  # mcp.ClientSession
        self._stack = contextlib.AsyncExitStack()

    async def list_tools(self) -> list[MCPToolDefinition]:
        await self._ensure_initialized()
        result = await self._session.list_tools()
        return [
            MCPToolDefinition(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema) if tool.inputSchema else {},
            )
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self._ensure_initialized()
        result = await self._session.call_tool(name, arguments)
        # Extract text content; fall back to structured dict for non-text or multi-part results
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if len(texts) == 1 and not result.isError:
            return texts[0]
        return {
            "content": [c.model_dump() for c in result.content],
            "isError": result.isError,
        }

    async def close(self) -> None:
        await self._stack.aclose()
        self._session = None

    async def _ensure_initialized(self) -> None:
        if self._session is not None:
            return
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        merged_env = {**os.environ, **self.env} if self.env else None
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=merged_env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
