from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from agent.plugins import PluginCommandTool, PluginManager, PluginToolConfig
from agent.permissions import PermissionManager
from agent.tools.registry import ToolRegistry


async def test_plugin_manager_discovers_command_tool(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    script = plugin_dir / "echo.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "json.dump({'echo': payload['text']}, sys.stdout)\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo input",
                        "command": f"{sys.executable} {script.name}",
                        "input_schema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = PluginManager([tmp_path / "plugins"])
    tools = manager.load_tools()
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    result = await registry.execute(
        "plugin__demo__echo",
        {"text": "hello"},
        call_id="plugin-1",
    )

    assert not result.is_error
    assert json.loads(result.content) == {"echo": "hello"}


async def test_plugin_tool_uses_existing_permission_gate(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "tools": [
                    {
                        "name": "danger",
                        "description": "Danger",
                        "command": f"{sys.executable} -c 'print(1)'",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry(
        permission_manager=PermissionManager(
            tool_policies={"plugin__demo__danger": "deny"}
        )
    )
    for tool in PluginManager([tmp_path / "plugins"]).load_tools():
        registry.register(tool)

    result = await registry.execute(
        "plugin__demo__danger",
        {},
        call_id="plugin-2",
    )

    assert result.is_error
    assert "permission denied" in result.content


async def test_plugin_tool_kills_process_on_timeout(monkeypatch, tmp_path: Path) -> None:
    class HangingProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        async def communicate(self, _input: bytes) -> tuple[bytes, bytes]:
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -9
            return self.returncode

    process = HangingProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    tool = PluginCommandTool(
        plugin_name="demo",
        config=PluginToolConfig(
            name="hang",
            command=f"{sys.executable} -c 'pass'",
            timeout_s=0.01,
        ),
        plugin_dir=tmp_path,
    )

    try:
        await tool.execute({}, call_id="plugin-timeout")
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected plugin timeout")

    assert process.killed
    assert process.waited
