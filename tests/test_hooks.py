from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from agent.hooks import HookResult, HookRunner
from agent.loop import run_task
from agent.models import AgentConfig, Message, ModelTurn
from agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Unit tests for HookRunner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_runner_allows_everything() -> None:
    runner = HookRunner.empty()
    result = await runner.run("pre_tool_use", {"tool_name": "bash"})
    assert not result.denied


@pytest.mark.asyncio
async def test_hook_deny_returns_denied(tmp_path: Path) -> None:
    """A hook script that outputs {"decision": "deny"} should block the call."""
    hook_script = tmp_path / "deny.py"
    hook_script.write_text(
        "import json, sys\n"
        'json.dump({"decision": "deny", "reason": "test deny"}, sys.stdout)\n'
    )
    hook_script.chmod(hook_script.stat().st_mode | stat.S_IEXEC)

    hooks_toml = tmp_path / "hooks.toml"
    hooks_toml.write_text(
        f'[[pre_tool_use]]\ncommand = "{sys.executable} {hook_script}"\n'
    )

    runner = HookRunner.from_file(hooks_toml)
    result = await runner.run("pre_tool_use", {"tool_name": "bash"})
    assert result.denied
    assert "test deny" in result.reason


@pytest.mark.asyncio
async def test_hook_allow_when_stdout_empty(tmp_path: Path) -> None:
    """A hook that writes nothing to stdout should allow the action."""
    hook_script = tmp_path / "silent.py"
    hook_script.write_text("# silent\n")

    hooks_toml = tmp_path / "hooks.toml"
    hooks_toml.write_text(
        f'[[pre_tool_use]]\ncommand = "{sys.executable} {hook_script}"\n'
    )

    runner = HookRunner.from_file(hooks_toml)
    result = await runner.run("pre_tool_use", {"tool_name": "bash"})
    assert not result.denied


@pytest.mark.asyncio
async def test_hook_receives_payload(tmp_path: Path) -> None:
    """The hook script should receive the JSON payload on stdin."""
    capture_file = tmp_path / "captured.json"
    hook_script = tmp_path / "capture.py"
    hook_script.write_text(
        "import json, sys\n"
        f"data = json.load(sys.stdin)\n"
        f"open({str(capture_file)!r}, 'w').write(json.dumps(data))\n"
    )

    hooks_toml = tmp_path / "hooks.toml"
    hooks_toml.write_text(
        f'[[pre_tool_use]]\ncommand = "{sys.executable} {hook_script}"\n'
    )

    runner = HookRunner.from_file(hooks_toml)
    await runner.run("pre_tool_use", {"tool_name": "read_file", "arguments": {"path": "x.py"}})

    captured = json.loads(capture_file.read_text())
    assert captured["tool_name"] == "read_file"


@pytest.mark.asyncio
async def test_from_file_missing_path_returns_empty_runner(tmp_path: Path) -> None:
    runner = HookRunner.from_file(tmp_path / "nonexistent.toml")
    result = await runner.run("pre_tool_use", {})
    assert not result.denied


# ---------------------------------------------------------------------------
# Integration: hook denial propagates through run_task
# ---------------------------------------------------------------------------


class _DummyClient:
    """Model client that emits one tool call then stops."""

    def __init__(self) -> None:
        self._calls = 0

    async def run_turn(self, *, system_prompt, messages, tools, stream_handler=None):
        self._calls += 1
        if self._calls == 1:
            from agent.models import ToolCall
            return ModelTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})],
                stop_reason="tool_use",
            )
        return ModelTurn(text="done", tool_calls=[], stop_reason="end_turn")


@pytest.mark.asyncio
async def test_hook_denial_surfaces_as_error_in_run_task(tmp_path: Path) -> None:
    hook_script = tmp_path / "deny.py"
    hook_script.write_text(
        "import json, sys\n"
        'json.dump({"decision": "deny", "reason": "blocked by hook"}, sys.stdout)\n'
    )

    hooks_toml = tmp_path / "hooks.toml"
    hooks_toml.write_text(
        f'[[pre_tool_use]]\ncommand = "{sys.executable} {hook_script}"\n'
    )

    runner = HookRunner.from_file(hooks_toml)
    registry = ToolRegistry()

    result = await run_task(
        "do something",
        model_client=_DummyClient(),
        tool_registry=registry,
        config=AgentConfig(model_timeout_s=30),
        hook_runner=runner,
    )

    # The tool was blocked; the model received an error message and eventually
    # returned "done".
    tool_results = result.tool_results
    assert any(tr.is_error and "blocked by hook" in tr.content for tr in tool_results)
