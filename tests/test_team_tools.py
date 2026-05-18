from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agents.team import Team
from agent.core.loop import run_task
from agent.models import ModelTurn, RuntimeConfig, ToolCall
from agent.tools import build_default_registry
from agent.tools.team import TeamDispatchTool, TeamListTool


class ScriptedModelClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns
        self.calls = 0
        self.tool_batches: list[list[str]] = []

    async def run_turn(self, **kwargs) -> ModelTurn:
        self.tool_batches.append(
            [
                str(tool.get("function", {}).get("name", ""))
                for tool in kwargs.get("tools", [])
            ]
        )
        turn = self.turns[self.calls]
        self.calls += 1
        return turn


@pytest.mark.asyncio
async def test_team_dispatch_tool_returns_member_reply() -> None:
    team = Team()

    async def researcher(task: str, session_id: str) -> str:
        return f"researcher saw {task} in {session_id}"

    team.add_member("researcher", researcher)
    team.start()
    try:
        tool = TeamDispatchTool(team, default_timeout_s=2.0)
        result = await tool.execute(
            {
                "to_agent": "researcher",
                "task": "inspect the loop",
                "timeout_s": 2.0,
            },
            call_id="call-team",
        )
    finally:
        await team.shutdown()

    payload = json.loads(result.content)
    assert payload["data"]["to_agent"] == "researcher"
    assert payload["data"]["reply"].startswith("researcher saw inspect the loop")
    assert not result.is_error


@pytest.mark.asyncio
async def test_main_loop_can_call_team_dispatch() -> None:
    team = Team()

    async def reviewer(task: str, session_id: str) -> str:
        return f"review result for {task}"

    team.add_member("reviewer", reviewer)
    team.start()
    try:
        registry = build_default_registry(team=team)
        client = ScriptedModelClient(
            [
                ModelTurn(
                    text="I'll ask the reviewer.",
                    tool_calls=[
                        ToolCall(
                            id="call-team",
                            name="team_dispatch",
                            arguments={
                                "to_agent": "reviewer",
                                "task": "check the design",
                                "timeout_s": 2.0,
                            },
                        )
                    ],
                ),
                ModelTurn(text="Reviewer says the design is clear.", tool_calls=[]),
            ]
        )

        result = await run_task(
            "coordinate with the team",
            model_client=client,
            tool_registry=registry,
            config=RuntimeConfig(max_iterations=4, stream=False),
        )
    finally:
        await team.shutdown()

    assert result.final_text == "Reviewer says the design is clear."
    assert "team_dispatch" in client.tool_batches[0]
    assert result.tool_results[0].name == "team_dispatch"
    assert "review result for check the design" in result.tool_results[0].content


@pytest.mark.asyncio
async def test_team_list_tool_returns_registered_members() -> None:
    team = Team()

    async def noop(task: str, session_id: str) -> str:
        return "ok"

    team.add_member("researcher", noop, role="Reads code and summarizes findings.")

    tool = TeamListTool(team)
    result = await tool.execute({}, call_id="call-list")

    payload = json.loads(result.content)
    assert payload["data"]["members"] == [
        {"agent_id": "researcher", "role": "Reads code and summarizes findings."}
    ]


@pytest.mark.asyncio
async def test_team_persists_parent_session_in_child_session_id(tmp_path: Path) -> None:
    team = Team(log_path=tmp_path / "team.jsonl", parent_session_id="main-session")
    seen_session_ids: list[str] = []

    async def worker(task: str, session_id: str) -> str:
        seen_session_ids.append(session_id)
        return "ok"

    team.add_member("worker", worker)
    team.start()
    try:
        await team.dispatch("worker", "ping", timeout_s=2.0)
    finally:
        await team.shutdown()

    assert seen_session_ids[0].startswith("team:main-session:worker:")
