"""mcp.client — MCP stdio 客户端（官方 SDK 封装）。

``StdioMCPClient`` 通过 ``mcp`` 官方 Python SDK 连接本地 MCP 服务器进程：
  - 懒初始化：首次调用 list_tools() 或 call_tool() 时才启动进程
  - 自动重连：调用失败时重置 session 并重试一次，应对子进程崩溃场景
  - 使用 AsyncExitStack 管理生命周期，close() 负责清理进程和 session
  - list_tools() 返回工具定义列表（MCPToolDefinition）
  - call_tool() 返回工具执行结果（文本或结构化 dict）

MCP 协议握手流程（由官方 SDK 处理）：
  initialize → notifications/initialized → tools/list / tools/call
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


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
        return await self._with_retry(self._list_tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._with_retry(lambda: self._call_tool(name, arguments))

    async def close(self) -> None:
        await self._stack.aclose()
        self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _list_tools(self) -> list[MCPToolDefinition]:
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

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self._ensure_initialized()
        result = await self._session.call_tool(name, arguments)
        # 单条纯文本直接返回字符串；多段或报错返回结构化 dict
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if len(texts) == 1 and not result.isError:
            return texts[0]
        return {
            "content": [c.model_dump() for c in result.content],
            "isError": result.isError,
        }

    async def _with_retry(self, fn: Any) -> Any:
        """调用失败时重置 session 并重试一次，应对子进程意外崩溃的场景。"""
        try:
            return await fn()
        except Exception as exc:
            logger.warning(
                "MCP server %r call failed (%s), resetting session and retrying once",
                self.name,
                exc,
            )
            await self._reset()
            # 第二次失败直接向上抛，让调用方处理
            return await fn()

    async def _reset(self) -> None:
        """关闭当前 session 和子进程，重置状态以便下次懒初始化重新连接。"""
        with contextlib.suppress(Exception):
            await self._stack.aclose()
        self._session = None
        self._stack = contextlib.AsyncExitStack()

    async def _ensure_initialized(self) -> None:
        if self._session is not None:
            return
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        # 合并父进程环境变量与服务器专属 env，专属 env 优先级更高
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
        logger.debug("MCP server %r initialized", self.name)
