from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

import pytest
from pydantic import BaseModel

from agent.core.tool_executor import ToolCallExecutor
from agent.hooks import HookResult
from agent.models import ToolCall
from agent.tools.base import Tool
from agent.tools.registry import ToolRegistry


class _RecordingHooks:
    def __init__(self, *, denied_tools: set[str] | None = None) -> None:
        self.denied_tools = denied_tools or set()
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def run(self, event: str, payload: dict[str, Any]) -> HookResult:
        self.events.append((event, payload))
        if event == "pre_tool_use" and payload["tool_name"] in self.denied_tools:
            return HookResult(denied=True, reason="blocked")
        return HookResult()


class _Input(BaseModel):
    label: str


class _SafeTool(Tool):
    name = "safe_tool"
    description = "A concurrency-safe test tool."
    input_model = _Input

    def __init__(self, both_started: asyncio.Event, starts: list[str]) -> None:
        self.both_started = both_started
        self.starts = starts

    async def run(self, arguments: BaseModel) -> str:
        parsed = _Input.model_validate(arguments)
        self.starts.append(parsed.label)
        if len(self.starts) == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
        return parsed.label

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class _UnsafeTool(Tool):
    name = "unsafe_tool"
    description = "A serial test tool."
    input_model = _Input

    async def run(self, arguments: BaseModel) -> str:
        parsed = _Input.model_validate(arguments)
        return parsed.label


@pytest.mark.asyncio
async def test_tool_call_executor_runs_hooks_and_denies_before_registry() -> None:
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    hooks = _RecordingHooks(denied_tools={"unsafe_tool"})
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=hooks,
        session_id="session-1",
    )

    results = await executor.execute_many(
        [ToolCall(id="call-1", name="unsafe_tool", arguments={"label": "x"})]
    )

    assert len(results) == 1
    assert results[0].is_error is True
    assert results[0].content == "[hook denied]: blocked"
    assert [event for event, _ in hooks.events] == ["pre_tool_use"]


@pytest.mark.asyncio
async def test_tool_call_executor_batches_concurrency_safe_calls_in_order() -> None:
    both_started = asyncio.Event()
    starts: list[str] = []
    registry = ToolRegistry()
    registry.register(_SafeTool(both_started, starts))
    hooks = _RecordingHooks()
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=hooks,
        session_id="session-1",
    )

    started_at = monotonic()
    results = await executor.execute_many(
        [
            ToolCall(id="call-1", name="safe_tool", arguments={"label": "one"}),
            ToolCall(id="call-2", name="safe_tool", arguments={"label": "two"}),
        ]
    )

    assert monotonic() - started_at < 0.2
    assert [result.content for result in results] == ["one", "two"]
    assert [event for event, _ in hooks.events] == [
        "pre_tool_use",
        "pre_tool_use",
        "post_tool_use",
        "post_tool_use",
    ]
