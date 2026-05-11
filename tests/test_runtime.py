from __future__ import annotations

import sys
from types import SimpleNamespace
import json

from agent.config import AgentSettings
from agent.mcp import MCPServerConfig
from agent.profiles import FallbackModelClient, ModelProfile
from agent.runtime.builder import _attach_mcp_tools, _build_runtime, _build_stream_output
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.utils import _configure_readline


def test_configure_readline_applies_bindings(monkeypatch) -> None:
    bindings: list[str] = []

    fake_readline = SimpleNamespace(
        parse_and_bind=bindings.append,
    )
    monkeypatch.setitem(sys.modules, "readline", fake_readline)

    _configure_readline()

    assert bindings == [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]


def test_configure_readline_ignores_backend_errors(monkeypatch) -> None:
    calls: list[str] = []

    def parse_and_bind(binding: str) -> None:
        calls.append(binding)
        raise RuntimeError("unsupported")

    fake_readline = SimpleNamespace(parse_and_bind=parse_and_bind)
    monkeypatch.setitem(sys.modules, "readline", fake_readline)

    _configure_readline()

    assert len(calls) == 4


def test_build_runtime_registers_subagent_tool_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False),
    )

    assert runtime["tool_registry"].get("subagent_run") is not None


def test_build_runtime_omits_subagent_tool_at_max_depth(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False, subagent_max_depth=1),
        subagent_depth=1,
    )

    assert runtime["tool_registry"].get("subagent_run") is None


def test_build_runtime_uses_fallback_client_for_multiple_profiles(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            model_profiles=[
                ModelProfile(name="primary", model="openai/primary"),
                ModelProfile(name="backup", model="openai/backup"),
            ],
        ),
    )

    assert isinstance(runtime["model_client"], FallbackModelClient)


def test_build_stream_output_uses_markdown_renderer_for_streaming_markdown() -> None:
    output = _build_stream_output(
        AgentSettings(
            stream=True,
            render_markdown=True,
            markdown_streaming=True,
        )
    )

    assert isinstance(output.renderer, MarkdownStreamRenderer)
    assert output.handler == output.renderer.write


def test_build_stream_output_falls_back_to_raw_stream_printer() -> None:
    output = _build_stream_output(
        AgentSettings(
            stream=True,
            render_markdown=False,
            markdown_streaming=True,
        )
    )

    assert output.renderer is None
    assert output.handler is not None


def test_build_stream_output_disabled_when_stream_is_false() -> None:
    output = _build_stream_output(
        AgentSettings(
            stream=False,
            render_markdown=True,
            markdown_streaming=True,
        )
    )

    assert output.renderer is None
    assert output.handler is None


def test_build_runtime_registers_plugin_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "command": f"{sys.executable} -c 'print(1)'",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            plugin_dirs=[str(tmp_path / "plugins")],
        ),
    )

    assert runtime["tool_registry"].get("plugin__demo__echo") is not None
    assert "plugin__demo__echo" in runtime["config"].system_prompt


def test_build_runtime_registers_worktree_tools_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            enable_worktree_isolation=True,
        ),
    )

    assert runtime["tool_registry"].get("worktree_create") is not None
    assert runtime["tool_registry"].get("worktree_cleanup") is not None


async def test_attach_mcp_tools_registers_discovered_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    server = tmp_path / "server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        text = request.get("params", {}).get("arguments", {}).get("text", "")
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
""".strip(),
        encoding="utf-8",
    )
    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            mcp_servers=[
                MCPServerConfig(
                    name="fake",
                    command=sys.executable,
                    args=[str(server)],
                )
            ],
        )
    )

    router = await _attach_mcp_tools(runtime, runtime["settings"])

    try:
        result = await runtime["tool_registry"].execute(
            "mcp__fake__echo",
            {"text": "hello"},
            call_id="mcp-main",
        )
    finally:
        if router is not None:
            await router.close()

    assert not result.is_error
    assert "hello" in result.content
    assert "mcp__fake__echo" in runtime["config"].system_prompt
