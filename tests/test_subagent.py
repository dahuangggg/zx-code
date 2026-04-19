from __future__ import annotations

import json

import pytest

from agent.lanes import LaneScheduler
from agent.subagent import SubagentRecursionError, SubagentRunner
from agent.tools import build_default_registry


async def test_subagent_runner_uses_isolated_session_id() -> None:
    calls: list[tuple[str, str, int]] = []

    async def run_agent_text(task: str, session_id: str, depth: int) -> str:
        calls.append((task, session_id, depth))
        return "child result"

    runner = SubagentRunner(
        run_agent_text=run_agent_text,
        parent_session_id="parent-session",
        max_depth=1,
        current_depth=0,
    )

    result = await runner.run("inspect auth flow", label="auth")

    assert result.final_text == "child result"
    assert result.session_id.startswith("parent-session:subagent:auth:")
    assert calls == [("inspect auth flow", result.session_id, 1)]


async def test_subagent_runner_rejects_recursive_depth() -> None:
    async def run_agent_text(task: str, session_id: str, depth: int) -> str:
        return "should not run"

    runner = SubagentRunner(
        run_agent_text=run_agent_text,
        parent_session_id="parent",
        max_depth=1,
        current_depth=1,
    )

    with pytest.raises(SubagentRecursionError):
        await runner.run("too deep")


async def test_subagent_runner_records_subagent_lane() -> None:
    scheduler = LaneScheduler()

    async def run_agent_text(task: str, session_id: str, depth: int) -> str:
        return f"{task}:{depth}"

    runner = SubagentRunner(
        run_agent_text=run_agent_text,
        parent_session_id="parent",
        lane_scheduler=scheduler,
    )

    result = await runner.run("scan files", label="scanner")

    assert result.final_text == "scan files:1"
    assert scheduler.history[-1].lane == "subagent"

    await scheduler.close()


async def test_subagent_tool_executes_runner() -> None:
    async def run_agent_text(task: str, session_id: str, depth: int) -> str:
        return f"done: {task}"

    runner = SubagentRunner(
        run_agent_text=run_agent_text,
        parent_session_id="parent",
    )
    registry = build_default_registry(subagent_runner=runner)

    result = await registry.execute(
        "subagent_run",
        {"task": "summarize gateway", "label": "reader"},
        call_id="sub-1",
    )

    payload = json.loads(result.content)
    assert not result.is_error
    assert payload["final_text"] == "done: summarize gateway"
    assert payload["session_id"].startswith("parent:subagent:reader:")
