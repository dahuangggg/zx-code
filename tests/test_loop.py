from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.loop import run_task
from agent.models import AgentConfig, Message, ModelTurn, ToolCall
from agent.recovery import MaxIterationsExceededError
from agent.tools import build_default_registry


class ScriptedModelClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns
        self.calls = 0

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        stream_handler=None,
    ) -> ModelTurn:
        turn = self.turns[self.calls]
        self.calls += 1
        if stream_handler and turn.text:
            stream_handler(turn.text)
        return turn


@pytest.mark.asyncio
async def test_loop_runs_tool_call_and_returns_final_answer(tmp_path: Path) -> None:
    target = tmp_path / "answer.txt"
    registry = build_default_registry()
    client = ScriptedModelClient(
        [
            ModelTurn(
                text="I'll write the file first.",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={
                            "path": str(target),
                            "content": "done\n",
                        },
                    )
                ],
            ),
            ModelTurn(
                text="Done. The file has been written.",
                tool_calls=[],
            ),
        ]
    )

    streamed: list[str] = []

    result = await run_task(
        "write a file",
        model_client=client,
        tool_registry=registry,
        config=AgentConfig(max_iterations=4),
        stream_handler=streamed.append,
    )

    assert target.read_text() == "done\n"
    assert result.final_text == "Done. The file has been written."
    assert len(result.tool_results) == 1
    assert streamed == [
        "I'll write the file first.",
        "Done. The file has been written.",
    ]


@pytest.mark.asyncio
async def test_loop_raises_on_max_iterations() -> None:
    registry = build_default_registry()
    client = ScriptedModelClient(
        [
            ModelTurn(
                text="keep going",
                tool_calls=[ToolCall(id="call-1", name="grep", arguments={"pattern": "x"})],
            ),
            ModelTurn(
                text="still going",
                tool_calls=[ToolCall(id="call-2", name="grep", arguments={"pattern": "y"})],
            ),
        ]
    )

    with pytest.raises(MaxIterationsExceededError):
        await run_task(
            "loop forever",
            model_client=client,
            tool_registry=registry,
            config=AgentConfig(max_iterations=1),
        )

