"""mcp.router — 多 MCP 服务器路由与工具代理。

``MCPToolRouter`` 管理多个 MCP 服务器，将它们的工具整合进 ToolRegistry：
  - ``discover_tools()`` 并发连接所有服务器，收集工具列表，
    为每个工具创建 ``MCPProxyTool``；单个服务器失败时记录日志并跳过，
    不影响其他服务器的工具注册
  - ``MCPProxyTool.execute()`` 将工具调用转发到对应服务器
  - 工具命名：``mcp__<server_name>__<tool_name>``，命名冲突时在 discover 阶段报错
  - 权限检查由 ToolRegistry 在调用 execute() 之前完成；router.call_tool()
    仅在被直接调用（绕开 registry）时才执行权限检查

``close()`` 断开所有服务器连接，释放子进程资源。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from agent.mcp.client import MCPToolDefinition
from agent.models import ToolResult
from agent.tools.base import Tool

if TYPE_CHECKING:
    from agent.permissions import PermissionManager

logger = logging.getLogger(__name__)


class MCPClient(Protocol):
    async def list_tools(self) -> list[MCPToolDefinition]:
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        ...

    async def close(self) -> None:
        ...


class MCPProxyTool(Tool):
    name: str
    description: str
    input_model = object  # type: ignore[assignment]

    def __init__(
        self,
        *,
        full_name: str,
        server_name: str,
        tool: MCPToolDefinition,
        router: "MCPToolRouter",
    ) -> None:
        self.name = full_name
        self.description = tool.description or f"MCP tool {server_name}.{tool.name}"
        self.server_name = server_name
        self.tool_name = tool.name
        self.input_schema = tool.input_schema or {"type": "object", "properties": {}}
        self.router = router

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        # 权限检查已由 ToolRegistry 在调用 execute() 之前完成，此处直接转发
        output = await self.router.call_tool(
            self.name,
            arguments,
            skip_permission=True,
        )
        content = output if isinstance(output, str) else json.dumps(
            output,
            indent=2,
            sort_keys=True,
        )
        is_error = (
            isinstance(output, dict)
            and bool(output.get("isError") or output.get("is_error"))
        )
        return ToolResult(
            call_id=call_id,
            name=self.name,
            content=content,
            is_error=is_error,
        )

    async def run(self, arguments: Any) -> str | dict[str, Any]:
        raise NotImplementedError("MCPProxyTool overrides execute")


class MCPToolRouter:
    def __init__(
        self,
        clients: dict[str, MCPClient],
        *,
        permission_manager: "PermissionManager | None" = None,
    ) -> None:
        self.clients = clients
        self.permission_manager = permission_manager
        self._tool_map: dict[str, tuple[str, str]] = {}

    async def discover_tools(self) -> list[MCPProxyTool]:
        """并发问询所有服务器的工具列表；单个服务器失败时跳过并记录日志。"""
        server_names = list(self.clients.keys())
        # 并发拉取所有服务器的工具列表，return_exceptions 保证单个失败不中断其他
        results = await asyncio.gather(
            *[self.clients[name].list_tools() for name in server_names],
            return_exceptions=True,
        )

        discovered: list[MCPProxyTool] = []
        for server_name, result in zip(server_names, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "MCP server %r discovery failed, skipping: %s",
                    server_name,
                    result,
                )
                continue

            for tool in result:
                full_name = self.format_name(server_name, tool.name)
                # 命名冲突在 discover 阶段报错，比运行时静默覆盖更容易排查
                if full_name in self._tool_map:
                    existing_server = self._tool_map[full_name][0]
                    raise ValueError(
                        f"MCP tool name collision: {full_name!r} registered by "
                        f"{existing_server!r} conflicts with {server_name!r}. "
                        f"Rename one of the servers in your config to resolve."
                    )
                self._tool_map[full_name] = (server_name, tool.name)
                discovered.append(
                    MCPProxyTool(
                        full_name=full_name,
                        server_name=server_name,
                        tool=tool,
                        router=self,
                    )
                )

        logger.debug(
            "MCP discovery complete: %d tools from %d/%d servers",
            len(discovered),
            sum(1 for r in results if not isinstance(r, BaseException)),
            len(server_names),
        )
        return discovered

    async def call_tool(
        self,
        full_name: str,
        arguments: dict[str, Any],
        *,
        skip_permission: bool = False,
    ) -> Any:
        """转发工具调用到对应服务器。

        skip_permission=True 由 MCPProxyTool.execute() 传入，表示 ToolRegistry
        层已完成权限检查。直接调用 router（绕开 registry）时保持默认 False。
        """
        if self.permission_manager is not None and not skip_permission:
            check = self.permission_manager.decide(full_name, arguments)
            if check.decision == "deny":
                raise PermissionError(
                    f"MCP tool call denied by permission policy: {full_name} — {check.reason}"
                )
            # "ask" 在此层视为拒绝：router 没有交互式审批回调；
            # 通过 registry 注册的 MCPProxyTool 会走 ToolRegistry 的正常审批流程
            if check.decision == "ask":
                raise PermissionError(
                    f"MCP tool call requires approval (called outside registry): {full_name}"
                )
        server_name, tool_name = self.parse_name(full_name)
        client = self.clients[server_name]
        return await client.call_tool(tool_name, arguments)

    async def close(self) -> None:
        """并发关闭所有服务器连接，单个关闭失败不阻塞其他。"""
        results = await asyncio.gather(
            *[client.close() for client in self.clients.values()],
            return_exceptions=True,
        )
        for server_name, result in zip(self.clients.keys(), results):
            if isinstance(result, BaseException):
                logger.warning("MCP server %r failed to close cleanly: %s", server_name, result)

    def parse_name(self, full_name: str) -> tuple[str, str]:
        # 优先查 _tool_map（精确），找不到再按 __ 切割（兜底，支持 discover 前直接调用）
        if full_name in self._tool_map:
            return self._tool_map[full_name]
        parts = full_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            raise ValueError(f"invalid MCP tool name: {full_name}")
        return parts[1], parts[2]

    @staticmethod
    def format_name(server_name: str, tool_name: str) -> str:
        return f"mcp__{_safe_part(server_name)}__{_safe_part(tool_name)}"


def _safe_part(value: str) -> str:
    # 把非字母数字字符替换成 _，保证 LLM tool name 合法；注意不同原始名可能映射到同一结果
    # （如 "my-server" 和 "my.server" 都变成 "my_server"），冲突由 discover_tools 检测
    safe = []
    for char in value:
        if char.isalnum() or char == "_":
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "tool"
