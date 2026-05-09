from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

from agent.mcp import MCPToolDefinition, MCPToolRouter, StdioMCPClient
from agent.permissions import PermissionManager
from agent.tools.registry import ToolRegistry


class _FakeMCPClient:
    async def list_tools(self) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="echo",
                description="Echo input text",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return {"name": name, "arguments": arguments}

    async def close(self) -> None:
        return None


class _FailingMCPClient(_FakeMCPClient):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "remote failure",
                }
            ],
            "isError": True,
        }


async def test_mcp_router_discovers_tools_and_executes_via_registry() -> None:
    router = MCPToolRouter({"local": _FakeMCPClient()})
    registry = ToolRegistry()

    for tool in await router.discover_tools():
        registry.register(tool)

    assert registry.get("mcp__local__echo") is not None

    result = await registry.execute(
        "mcp__local__echo",
        {"text": "hello"},
        call_id="mcp-1",
    )

    assert not result.is_error
    assert json.loads(result.content) == {
        "name": "echo",
        "arguments": {"text": "hello"},
    }


async def test_mcp_tool_propagates_remote_error_flag() -> None:
    router = MCPToolRouter({"local": _FailingMCPClient()})
    registry = ToolRegistry()

    for tool in await router.discover_tools():
        registry.register(tool)

    result = await registry.execute(
        "mcp__local__echo",
        {"text": "hello"},
        call_id="mcp-error",
    )

    assert result.is_error
    assert json.loads(result.content)["isError"] is True


async def test_mcp_tool_uses_existing_permission_gate() -> None:
    router = MCPToolRouter({"local": _FakeMCPClient()})
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            tool_policies={"mcp__local__echo": "deny"}
        )
    )

    for tool in await router.discover_tools():
        registry.register(tool)

    result = await registry.execute(
        "mcp__local__echo",
        {"text": "hello"},
        call_id="mcp-2",
    )

    assert result.is_error
    assert "permission denied" in result.content


async def test_mcp_tool_uses_registry_approval_without_second_denial() -> None:
    router = MCPToolRouter(
        {"local": _FakeMCPClient()},
        permission_manager=PermissionManager(
            tool_policies={"mcp__local__echo": "ask"}
        ),
    )
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            tool_policies={"mcp__local__echo": "ask"}
        ),
        approval_callback=lambda _check: True,
    )

    for tool in await router.discover_tools():
        registry.register(tool)

    result = await registry.execute(
        "mcp__local__echo",
        {"text": "hello"},
        call_id="mcp-approved",
    )

    assert not result.is_error
    assert json.loads(result.content)["arguments"] == {"text": "hello"}


async def test_mcp_router_denies_when_permission_manager_says_deny() -> None:
    pm = PermissionManager(tool_policies={"mcp__local__echo": "deny"})
    router = MCPToolRouter({"local": _FakeMCPClient()}, permission_manager=pm)
    await router.discover_tools()

    try:
        await router.call_tool("mcp__local__echo", {"text": "hi"})
        assert False, "expected PermissionError"
    except PermissionError as exc:
        assert "denied" in str(exc).lower()


async def test_mcp_router_allows_when_no_permission_manager() -> None:
    router = MCPToolRouter({"local": _FakeMCPClient()})
    await router.discover_tools()
    result = await router.call_tool("mcp__local__echo", {"text": "hi"})
    assert result is not None


async def test_mcp_router_allows_when_permission_manager_says_allow() -> None:
    pm = PermissionManager(
        tool_policies={"mcp__local__echo": "allow"},
        default_decision="deny",
    )
    router = MCPToolRouter({"local": _FakeMCPClient()}, permission_manager=pm)
    await router.discover_tools()
    result = await router.call_tool("mcp__local__echo", {"text": "ok"})
    assert result is not None


async def test_stdio_mcp_client_lists_and_calls_tools(tmp_path: Path) -> None:
    server = tmp_path / "fake_mcp_server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    # Notifications have no "id" — acknowledge but don't reply
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0.1"}}
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo input text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                }
            ]
        }
    elif method == "tools/call":
        params = request.get("params", {})
        result = {"content": [{"type": "text", "text": params["arguments"]["text"]}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
""".strip(),
        encoding="utf-8",
    )
    server.chmod(server.stat().st_mode | stat.S_IEXEC)

    client = StdioMCPClient(
        name="fake",
        command=sys.executable,
        args=[str(server)],
    )

    try:
        tools = await client.list_tools()
        result = await client.call_tool("echo", {"text": "hello"})
    finally:
        await client.close()

    assert tools[0].name == "echo"
    # Single text content is unwrapped to a plain string by StdioMCPClient
    assert result == "hello"
