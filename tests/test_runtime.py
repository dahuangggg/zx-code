from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agent.config import AgentSettings, ConfigLoader
from agent.models import Message
from agent.mcp import MCPServerConfig
from agent.profiles import FallbackModelClient, ModelProfile
from agent.providers.openai_client import OpenAIModelClient
from agent.runtime.builder import (
    CLIProgressReporter,
    _attach_mcp_tools,
    _build_runtime,
    _build_stream_output,
)
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.runner import _run_once, _run_repl
from agent.runtime.types import AgentRuntime
from agent.runtime.utils import _configure_readline
from agent.security import PolicyCompiler
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


def test_build_runtime_returns_typed_agent_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(AgentSettings(enable_memory=False, enable_todos=False))

    assert isinstance(runtime, AgentRuntime)
    assert runtime.settings is not None
    assert runtime.tool_registry is not None
    assert runtime.system_prompt
    assert runtime.session_store is not None


def test_build_runtime_omits_optional_state_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False, enable_tasks=False)
    )

    assert runtime.state.memory_store is None
    assert runtime.state.todo_manager is None
    assert runtime.state.task_store is None
    assert runtime.extensions.team is None


def test_build_runtime_registers_subagent_tool_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False),
    )

    assert runtime.tool_registry.get("subagent_run") is not None


def test_build_runtime_registers_team_tools_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            teams={
                "enabled": True,
                "members": [
                    {
                        "id": "researcher",
                        "role": "Reads code and summarizes findings.",
                    }
                ],
            },
        ),
    )

    assert runtime.tool_registry.get("team_list") is not None
    assert runtime.tool_registry.get("team_dispatch") is not None
    assert runtime.tool_registry.get("team_broadcast") is not None
    assert runtime.extensions.team is not None
    assert runtime.extensions.team.members_summary() == [
        {"agent_id": "researcher", "role": "Reads code and summarizes findings."}
    ]


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
            model="gpt-test-model",
            enable_memory=False,
            enable_todos=False,
        ),
        print_system_prompt=False,
        resume_session_id="demo",
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "zx-code" in output
    assert "gpt-test-model" in output
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
    assert "/undo" in output


@pytest.mark.asyncio
async def test_repl_undo_restores_latest_or_named_operation(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["/undo", "/undo operation-123", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    class FakeSession:
        def __init__(self) -> None:
            self.undos: list[str | None] = []

        def undo(self, operation_id: str | None = None):
            self.undos.append(operation_id)
            resolved_id = operation_id or "operation-latest"
            return resolved_id, tmp_path / "hello_world.py"

        async def close(self) -> None:
            return None

    session = FakeSession()

    async def fake_open(settings, **kwargs):
        return session

    monkeypatch.setattr("agent.runtime.runner.AgentSession.open", fake_open)

    exit_code = await _run_repl(
        settings=AgentSettings(enable_memory=False, enable_todos=False),
        print_system_prompt=False,
        resume_session_id="demo",
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert session.undos == [None, "operation-123"]
    assert "Undid operation-latest" in output
    assert "Undid operation-123" in output
    assert "Unknown command" not in output


@pytest.mark.asyncio
async def test_repl_resume_prints_recent_session_messages(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    settings = AgentSettings(enable_memory=False, enable_todos=False)
    store = SessionStore(tmp_path / settings.state.data_dir / "sessions")
    store.append_message("demo", Message.user("old question"))
    store.append_message("demo", Message.assistant("old answer"))
    store.append_message("demo", Message.tool("call-1", "bash", "tool output"))
    store.append_message("demo", Message.user("recent question"))
    store.append_message("demo", Message.assistant("recent answer"))

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
async def test_repl_opens_one_long_lived_session_for_multiple_inputs(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["first", "second", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    opens: list[str | None] = []

    class FakeSession:
        def __init__(self) -> None:
            self.runs: list[str] = []
            self.closes = 0

        async def run(self, task: str):
            self.runs.append(task)
            return SimpleNamespace(final_text=f"answer:{task}")

        async def close(self) -> None:
            self.closes += 1

    session = FakeSession()

    async def fake_open(settings, *, session_id=None, **kwargs):
        opens.append(session_id)
        return session

    monkeypatch.setattr("agent.runtime.runner.AgentSession.open", fake_open)

    exit_code = await _run_repl(
        settings=AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        print_system_prompt=False,
        resume_session_id="demo",
    )

    assert exit_code == 0
    assert opens == ["demo"]
    assert session.runs == ["first", "second"]
    assert session.closes == 1


@pytest.mark.asyncio
async def test_run_once_uses_fresh_cli_session_when_not_resuming(monkeypatch) -> None:
    seen_session_ids: list[str | None] = []
    generated = iter(["cli:one", "cli:two"])

    class FakeLaneScheduler:
        async def run(self, lane, run, *, job_id=None):
            return await run()

        async def close(self) -> None:
            return None

    async def fake_run_agent_text(task: str, *, settings, session_id=None, lane_scheduler=None, subagent_depth=0) -> str:
        seen_session_ids.append(session_id)
        return "ok"

    monkeypatch.setattr("agent.runtime.runner._new_cli_session_id", lambda: next(generated))
    monkeypatch.setattr("agent.runtime.runner.LaneScheduler", FakeLaneScheduler)
    monkeypatch.setattr("agent.runtime.runner._run_agent_text", fake_run_agent_text)

    settings = AgentSettings(session_id="configured", enable_memory=False, enable_todos=False)

    assert await _run_once("first", settings=settings, print_system_prompt=False) == 0
    assert await _run_once("second", settings=settings, print_system_prompt=False) == 0

    assert seen_session_ids == ["cli:one", "cli:two"]


@pytest.mark.asyncio
async def test_run_once_uses_resume_session_when_provided(monkeypatch) -> None:
    seen_session_ids: list[str | None] = []

    class FakeLaneScheduler:
        async def run(self, lane, run, *, job_id=None):
            return await run()

        async def close(self) -> None:
            return None

    async def fake_run_agent_text(task: str, *, settings, session_id=None, lane_scheduler=None, subagent_depth=0) -> str:
        seen_session_ids.append(session_id)
        return "ok"

    monkeypatch.setattr("agent.runtime.runner.LaneScheduler", FakeLaneScheduler)
    monkeypatch.setattr("agent.runtime.runner._run_agent_text", fake_run_agent_text)

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

    assert seen_session_ids == ["demo"]


def test_build_runtime_omits_subagent_tool_at_max_depth(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False, subagent_max_depth=1),
        subagent_depth=1,
    )

    assert runtime.tool_registry.get("subagent_run") is None


def test_build_runtime_uses_fallback_client_for_multiple_profiles(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            model_profiles=[
                ModelProfile(name="primary", model="gpt-primary"),
                ModelProfile(name="backup", model="gpt-backup"),
            ],
        ),
    )

    assert isinstance(runtime.services.model_client, FallbackModelClient)


def test_build_runtime_injects_context_summary_client(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            model="gpt-main",
            fallback_models="",
            compact_model="gpt-summary",
        ),
    )

    summary_client = runtime.services.context_guard.summary_client
    assert summary_client is not None
    assert summary_client is runtime.services.summary_model_client
    assert isinstance(summary_client.model_client, OpenAIModelClient)
    assert summary_client.model_client.model == "gpt-summary"


def test_build_runtime_creates_trace_logger_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            trace_enabled=True,
            trace_dir=".zx-code/logs/traces",
            trace_html=True,
        ),
    )

    trace_logger = runtime.services.trace_logger
    assert trace_logger.enabled is True
    assert trace_logger.session_id == "default"
    assert trace_logger.trace_dir == tmp_path / ".zx-code" / "logs" / "traces"
    assert trace_logger.html_enabled is True


def test_build_runtime_uses_disabled_trace_logger_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False),
    )

    trace_logger = runtime.services.trace_logger
    assert trace_logger.enabled is False
    assert not (tmp_path / ".zx-code" / "logs" / "traces").exists()


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
        {"tool_name": "read_file", "arguments": {"path": "src/agent/prompt.py"}},
    )
    reporter.handle("tool.end", {"tool_name": "read_file", "is_error": False})

    assert fake_console.statuses[0].started is True
    assert fake_console.statuses[0].stopped is True
    assert any("thinking" in item and "dots" in item for item in fake_console.prints)
    assert any("Read" in item and "src/agent/prompt.py" in item for item in fake_console.prints)
    assert not any("read_file" in item for item in fake_console.prints)
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

    assert runtime.tool_registry.get("plugin__demo__echo") is not None
    assert "plugin__demo__echo" in runtime.system_prompt


def test_untrusted_policy_skips_plugin_and_hook_loading(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    hooks_path = tmp_path / "hooks.toml"
    hooks_path.write_text('[[pre_tool_use]]\ncommand = "false"\n', encoding="utf-8")
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=tmp_path)

    def fail_plugin_load(self):
        raise AssertionError("plugin files must not be loaded")

    def fail_hook_load(path):
        raise AssertionError("hook file must not be loaded")

    monkeypatch.setattr("agent.runtime.builder.PluginManager.load_tools", fail_plugin_load)
    monkeypatch.setattr("agent.runtime.builder.HookRunner.from_file", fail_hook_load)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            plugin_dirs=[str(plugin_dir)],
            hooks_path=str(hooks_path),
        ),
        policy=policy,
    )

    assert runtime.policy is policy
    assert runtime.services.permission_manager.policy is policy
    assert runtime.services.hook_runner._hooks == {}


def test_build_runtime_registers_worktree_tools_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            enable_worktree_isolation=True,
        ),
    )

    assert runtime.tool_registry.get("worktree_create") is not None
    assert runtime.tool_registry.get("worktree_cleanup") is not None


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

    router = await _attach_mcp_tools(runtime, runtime.settings)

    try:
        result = await runtime.tool_registry.execute(
            "mcp__fake__echo",
            {"text": "hello"},
            call_id="mcp-main",
        )
    finally:
        if router is not None:
            await router.close()

    assert not result.is_error
    assert "hello" in result.content
    assert "mcp__fake__echo" in runtime.system_prompt


@pytest.mark.asyncio
async def test_untrusted_policy_skips_mcp_process_start(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=tmp_path)
    runtime = _build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            mcp_servers=[MCPServerConfig(name="blocked", command="must-not-run")],
        ),
        policy=policy,
    )

    def fail_router(*args, **kwargs):
        raise AssertionError("MCP router must not be constructed")

    monkeypatch.setattr("agent.runtime.builder.MCPToolRouter", fail_router)

    assert await _attach_mcp_tools(runtime, runtime.settings) is None
