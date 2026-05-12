from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agent.channels import InboundMessage
from agent.channels.gateway import build_session_key
from agent.config import AgentSettings
from agent.models import Message
from agent.mcp import MCPServerConfig
from agent.profiles import FallbackModelClient, ModelProfile
from agent.runtime.builder import (
    CLIProgressReporter,
    _attach_mcp_tools,
    _build_runtime,
    _build_stream_output,
)
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.runner import _run_once, _run_repl
from agent.runtime.utils import _configure_readline
from agent.state.sessions import SessionStore


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


@pytest.mark.asyncio
async def test_repl_prints_status_banner_and_session_prompt(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    prompts: list[str] = []
    inputs = iter(["/session", "exit"])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(inputs)

    monkeypatch.setattr("builtins.input", fake_input)

    exit_code = await _run_repl(
        settings=AgentSettings(
            model="openai/test-model",
            enable_memory=False,
            enable_todos=False,
        ),
        print_system_prompt=False,
        resume_session_id="demo",
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "zx-code" in output
    assert "openai/test-model" in output
    assert "demo" in output
    assert "resumed" in output
    assert "uv run agent --resume demo" in output
    assert prompts == [
        f"\033[36mzx-code\033[0m \033[2m[\033[0m\033[32m{tmp_path.name}\033[0m\033[2m]\033[0m \033[36m>\033[0m ",
        f"\033[36mzx-code\033[0m \033[2m[\033[0m\033[32m{tmp_path.name}\033[0m\033[2m]\033[0m \033[36m>\033[0m ",
    ]


@pytest.mark.asyncio
async def test_repl_help_command_lists_available_commands(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["/help", "exit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    exit_code = await _run_repl(
        settings=AgentSettings(enable_memory=False, enable_todos=False),
        print_system_prompt=False,
        resume_session_id="demo",
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "/help" in output
    assert "/session" in output
    assert "/clear" in output


@pytest.mark.asyncio
async def test_repl_resume_prints_recent_session_messages(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    settings = AgentSettings(enable_memory=False, enable_todos=False)
    session_key = build_session_key(
        agent_id=settings.routing.default_agent_id,
        channel="cli",
        account_id=settings.channel.account_id,
        peer_id="demo",
        dm_scope=settings.routing.dm_scope,
    )
    store = SessionStore(tmp_path / settings.state.data_dir / "sessions")
    store.append_message(session_key, Message.user("old question"))
    store.append_message(session_key, Message.assistant("old answer"))
    store.append_message(session_key, Message.tool("call-1", "bash", "tool output"))
    store.append_message(session_key, Message.user("recent question"))
    store.append_message(session_key, Message.assistant("recent answer"))

    exit_code = await _run_repl(
        settings=settings,
        print_system_prompt=False,
        resume_session_id="demo",
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "recent conversation" in output
    assert "recent question" in output
    assert "recent answer" in output
    assert "tool output" not in output


@pytest.mark.asyncio
async def test_run_once_uses_fresh_cli_session_when_not_resuming(monkeypatch) -> None:
    seen_peer_ids: list[str] = []
    generated = iter(["cli:one", "cli:two"])

    class FakeGateway:
        async def handle_inbound(
            self,
            inbound: InboundMessage,
            *,
            force_agent_id: str | None = None,
        ) -> None:
            seen_peer_ids.append(inbound.peer_id)

    class FakeLaneScheduler:
        async def close(self) -> None:
            return None

    monkeypatch.setattr("agent.runtime.runner._new_cli_session_id", lambda: next(generated))
    monkeypatch.setattr("agent.runtime.runner.LaneScheduler", FakeLaneScheduler)
    monkeypatch.setattr("agent.runtime.runner._build_gateway", lambda *args, **kwargs: FakeGateway())

    settings = AgentSettings(session_id="configured", enable_memory=False, enable_todos=False)

    assert await _run_once("first", settings=settings, print_system_prompt=False) == 0
    assert await _run_once("second", settings=settings, print_system_prompt=False) == 0

    assert seen_peer_ids == ["cli:one", "cli:two"]


@pytest.mark.asyncio
async def test_run_once_uses_resume_session_when_provided(monkeypatch) -> None:
    seen_peer_ids: list[str] = []

    class FakeGateway:
        async def handle_inbound(
            self,
            inbound: InboundMessage,
            *,
            force_agent_id: str | None = None,
        ) -> None:
            seen_peer_ids.append(inbound.peer_id)

    class FakeLaneScheduler:
        async def close(self) -> None:
            return None

    monkeypatch.setattr("agent.runtime.runner.LaneScheduler", FakeLaneScheduler)
    monkeypatch.setattr("agent.runtime.runner._build_gateway", lambda *args, **kwargs: FakeGateway())

    settings = AgentSettings(session_id="configured", enable_memory=False, enable_todos=False)

    assert (
        await _run_once(
            "continued",
            settings=settings,
            print_system_prompt=False,
            resume_session_id="demo",
        )
        == 0
    )

    assert seen_peer_ids == ["demo"]


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


def test_cli_progress_reporter_stops_thinking_and_prints_tools() -> None:
    class FakeStatus:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    class FakeConsole:
        def __init__(self) -> None:
            self.statuses: list[FakeStatus] = []
            self.prints: list[str] = []

        def status(self, message: str, *, spinner: str) -> FakeStatus:
            self.prints.append(f"status:{message}:{spinner}")
            status = FakeStatus()
            self.statuses.append(status)
            return status

        def print(self, message: str) -> None:
            self.prints.append(message)

    fake_console = FakeConsole()
    flushed: list[bool] = []
    reporter = CLIProgressReporter(fake_console, flush_output=lambda: flushed.append(True))

    reporter.handle("model.start", {"turn": 1})
    reporter.handle("model.chunk", {"turn": 1, "chunk": "hello"})
    reporter.handle(
        "tool.start",
        {"tool_name": "bash", "arguments": {"command": "ls -la"}},
    )
    reporter.handle("tool.end", {"tool_name": "bash", "is_error": False})

    assert fake_console.statuses[0].started is True
    assert fake_console.statuses[0].stopped is True
    assert any("thinking" in item and "dots" in item for item in fake_console.prints)
    assert any("bash" in item and "ls -la" in item for item in fake_console.prints)
    assert any("done" in item for item in fake_console.prints)
    assert flushed == [True]


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
