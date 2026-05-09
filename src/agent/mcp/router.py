"""mcp.router — 多 MCP 服务器路由与工具代理。

``MCPToolRouter`` 管理多个 MCP 服务器，将它们的工具整合进 ToolRegistry：
  - ``discover_tools()`` 并发连接所有服务器，收集工具列表，
    为每个工具创建 ``MCPProxyTool``
  - ``MCPProxyTool.execute()`` 将工具调用转发到对应服务器
  - 工具命名：``mcp__<server_name>__<tool_name>``
  - 权限检查通过 PermissionManager 集成（与内置工具一致）

``close()`` 断开所有服务器连接，释放子进程资源。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from agent.mcp.client import MCPToolDefinition
from agent.models import ToolResult
from agent.tools.base import Tool

if TYPE_CHECKING:
    from agent.permissions import PermissionManager


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
        output = await self.router.call_tool(
            self.name,
            arguments,
            skip_permission=True,
        )
        import json

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
        discovered: list[MCPProxyTool] = []
        for server_name, client in self.clients.items():
            for tool in await client.list_tools():
                full_name = self.format_name(server_name, tool.name)
                self._tool_map[full_name] = (server_name, tool.name)
                discovered.append(
                    MCPProxyTool(
                        full_name=full_name,
                        server_name=server_name,
                        tool=tool,
                        router=self,
                    )
                )
        return discovered

    async def call_tool(
        self,
        full_name: str,
        arguments: dict[str, Any],
        *,
        skip_permission: bool = False,
    ) -> Any:
        if self.permission_manager is not None and not skip_permission:
            check = self.permission_manager.decide(full_name, arguments)
            if check.decision == "deny":
                raise PermissionError(
                    f"MCP tool call denied by permission policy: {full_name} — {check.reason}"
                )
            # "ask" is treated as deny at the router level because there is no
            # interactive approval callback here; registry-registered MCPProxyTools
            # go through the normal approval flow in ToolRegistry.execute().
            if check.decision == "ask":
                raise PermissionError(
                    f"MCP tool call requires approval (called outside registry): {full_name}"
                )
        server_name, tool_name = self.parse_name(full_name)
        client = self.clients[server_name]
        return await client.call_tool(tool_name, arguments)

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()

    def parse_name(self, full_name: str) -> tuple[str, str]:
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
    safe = []
    for char in value:
        if char.isalnum() or char == "_":
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "tool"
