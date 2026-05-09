from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from pydantic import BaseModel

from agent.core.loop import run_task
from agent.models import RuntimeConfig, Message, ModelTurn, ToolCall
from agent.errors import MaxIterationsExceededError
from agent.state.sessions import SessionStore
from agent.tools.base import Tool
from agent.tools import build_default_registry
from agent.tools.registry import ToolRegistry


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
        config=RuntimeConfig(max_iterations=4),
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
            config=RuntimeConfig(max_iterations=1),
        )


@pytest.mark.asyncio
async def test_loop_preserves_continued_text_without_duplicate_stream(
    tmp_path: Path,
) -> None:
    registry = build_default_registry()
    session_store = SessionStore(tmp_path / "sessions")
    client = ScriptedModelClient(
        [
            ModelTurn(text="first chunk ", tool_calls=[], stop_reason="length"),
            ModelTurn(text="second chunk", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    streamed: list[str] = []

    result = await run_task(
        "continue",
        model_client=client,
        tool_registry=registry,
        config=RuntimeConfig(session_id="continuation-test"),
        stream_handler=streamed.append,
        session_store=session_store,
    )

    assert result.final_text == "first chunk second chunk"
    assert streamed == ["first chunk ", "second chunk"]
    assistant_messages = [
        message
        for message in session_store.rebuild_messages("continuation-test")
        if message.role == "assistant"
    ]
    assert assistant_messages[-1].content == "first chunk second chunk"


class _SlowSafeInput(BaseModel):
    label: str


class _SlowSafeTool(Tool):
    name = "slow_safe"
    description = "A slow read-only probe."
    input_model = _SlowSafeInput

    def __init__(self, both_started: asyncio.Event, starts: list[str]) -> None:
        self.both_started = both_started
        self.starts = starts

    async def run(self, arguments: BaseModel) -> str:
        parsed = _SlowSafeInput.model_validate(arguments)
        self.starts.append(parsed.label)
        if len(self.starts) == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
        return parsed.label

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


@pytest.mark.asyncio
async def test_loop_runs_concurrency_safe_tool_calls_together() -> None:
    both_started = asyncio.Event()
    starts: list[str] = []
    registry = ToolRegistry()
    registry.register(_SlowSafeTool(both_started, starts))
    client = ScriptedModelClient(
        [
            ModelTurn(
                text="checking",
                tool_calls=[
                    ToolCall(id="call-1", name="slow_safe", arguments={"label": "one"}),
                    ToolCall(id="call-2", name="slow_safe", arguments={"label": "two"}),
                ],
            ),
            ModelTurn(text="done", tool_calls=[]),
        ]
    )

    started_at = monotonic()
    result = await run_task(
        "run safe probes",
        model_client=client,
        tool_registry=registry,
        config=RuntimeConfig(max_iterations=3, stream=False),
    )

    assert monotonic() - started_at < 0.2
    assert result.final_text == "done"
    assert [tool_result.content for tool_result in result.tool_results] == ["one", "two"]
    assert all(not tool_result.is_error for tool_result in result.tool_results)
