from __future__ import annotations

import asyncio
import json
from inspect import isawaitable
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from pydantic import BaseModel

from agent.core.loop import run_task
from agent.debuglog import DebugLog
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
            result = stream_handler(turn.text)
            if isawaitable(result):
                await result
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
async def test_loop_emits_model_and_tool_progress_events(tmp_path: Path) -> None:
    target = tmp_path / "answer.txt"
    registry = build_default_registry()
    client = ScriptedModelClient(
        [
            ModelTurn(
                text="writing",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": str(target), "content": "done\n"},
                    )
                ],
            ),
            ModelTurn(text="done", tool_calls=[]),
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    await run_task(
        "write a file",
        model_client=client,
        tool_registry=registry,
        config=RuntimeConfig(max_iterations=4, stream=False),
        progress_handler=lambda event, payload: events.append((event, payload)),
    )

    event_names = [event for event, _ in events]
    assert event_names == [
        "model.start",
        "model.end",
        "tool.start",
        "tool.end",
        "model.start",
        "model.end",
    ]
    assert events[2][1]["tool_name"] == "write_file"
    assert events[3][1]["is_error"] is False


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


@pytest.mark.asyncio
async def test_debug_log_records_prompt_model_and_tool_events(tmp_path: Path) -> None:
    target = tmp_path / "answer.txt"
    log_path = tmp_path / "debug.jsonl"
    registry = build_default_registry()
    client = ScriptedModelClient(
        [
            ModelTurn(
                text="writing",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": str(target), "content": "done\n"},
                    )
                ],
            ),
            ModelTurn(text="done", tool_calls=[]),
        ]
    )

    await run_task(
        "write a file",
        model_client=client,
        tool_registry=registry,
        config=RuntimeConfig(
            system_prompt="system",
            max_iterations=4,
            session_id="debug-session",
        ),
        debug_log=DebugLog(log_path, session_id="debug-session"),
    )

    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]

    assert [event["event"] for event in events] == [
        "run.system_prompt",
        "run.user_message",
        "run.model_input",
        "run.assistant_message",
        "tool.call.requested",
        "tool.hook.pre",
        "tool.call.result",
        "tool.hook.post",
        "run.model_input",
        "run.assistant_message",
    ]
    assert {event["session_id"] for event in events} == {"debug-session"}
    assert events[0]["payload"]["system_prompt"] == "system"
    assert events[1]["payload"]["message"]["content"] == "write a file"
    assert events[4]["payload"]["call"]["name"] == "write_file"
    tool_result = json.loads(events[6]["payload"]["result"]["content"])
    assert tool_result["path"] == str(target)
