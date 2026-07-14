from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

import pytest
from pydantic import BaseModel

from agent.core.tool_executor import ToolCallExecutor
from agent.debuglog import DebugLog
from agent.hooks import HookResult
from agent.models import ToolCall
from agent.operations import FileCheckpointStore, OperationJournal
from agent.tools import build_default_registry
from agent.trace import TraceLogger
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


class _BlockedTool(_UnsafeTool):
    name = "blocked_tool"


class _LargeOutputTool(Tool):
    name = "large_output"
    description = "A tool that returns a large envelope payload."
    input_model = _Input

    async def run(self, arguments: BaseModel) -> str:
        return self.success(
            data={"content": "\n".join(str(index) for index in range(100))},
            text="large output",
        )


@pytest.mark.asyncio
async def test_tool_call_executor_runs_hooks_and_denies_before_registry() -> None:
    """hook deny 必须在工具执行前短路，同时仍返回可配对的 error envelope。"""
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    registry.register(_BlockedTool())
    hooks = _RecordingHooks(denied_tools={"unsafe_tool"})
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=hooks,
        session_id="session-1",
    )

    results, metrics = await executor.execute_many(
        [ToolCall(id="call-1", name="unsafe_tool", arguments={"label": "x"})]
    )

    assert len(results) == 1
    assert results[0].is_error is True
    denied_envelope = json.loads(results[0].content)
    assert denied_envelope["status"] == "error"
    assert denied_envelope["text"] == "[hook denied]: blocked"
    assert denied_envelope["error"]["message"] == "[hook denied]: blocked"
    assert [event for event, _ in hooks.events] == ["pre_tool_use"]


@pytest.mark.asyncio
async def test_tool_call_executor_batches_concurrency_safe_calls_in_order() -> None:
    """并发安全工具可以批量执行，但返回顺序仍必须匹配模型 tool_calls 顺序。"""
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
    results, metrics = await executor.execute_many(
        [
            ToolCall(id="call-1", name="safe_tool", arguments={"label": "one"}),
            ToolCall(id="call-2", name="safe_tool", arguments={"label": "two"}),
        ]
    )

    assert monotonic() - started_at < 0.2
    assert [json.loads(result.content)["text"] for result in results] == ["one", "two"]
    assert len(metrics) == 2
    assert all(m.duration_ms >= 0 for m in metrics)
    assert [event for event, _ in hooks.events] == [
        "pre_tool_use",
        "pre_tool_use",
        "post_tool_use",
        "post_tool_use",
    ]


@pytest.mark.asyncio
async def test_tool_call_executor_centralizes_large_output_truncation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    registry = ToolRegistry()
    registry.register(_LargeOutputTool())
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        project_root=tmp_path,
    )

    results, _ = await executor.execute_many(
        [ToolCall(id="call-1", name="large_output", arguments={"label": "x"})]
    )

    envelope = results[0].envelope
    assert results[0].status == "partial"
    assert envelope.status == "partial"
    assert envelope.data["truncated"] is True
    assert envelope.data["truncation"]["original_lines"] == 100
    assert envelope.data["truncation"]["full_output_path"].startswith(
        ".zx-code/artifacts/tool-output/"
    )


@pytest.mark.asyncio
async def test_tool_call_executor_logs_structured_tool_result_without_outer_status(
    tmp_path,
) -> None:
    """trace 只记录 envelope，不在外层重复 status/is_error。"""
    log_path = tmp_path / "debug.jsonl"
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        debug_log=DebugLog(log_path, session_id="session-1"),
    )

    await executor.execute_many(
        [ToolCall(id="call-1", name="unsafe_tool", arguments={"label": "x"})]
    )

    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    tool_result = next(event for event in events if event["event"] == "tool.result")

    assert tool_result["payload"]["call_id"] == "call-1"
    assert tool_result["payload"]["name"] == "unsafe_tool"
    assert tool_result["payload"]["envelope"]["status"] == "success"
    assert tool_result["payload"]["envelope"]["text"] == "x"
    assert "status" not in tool_result["payload"]
    assert "is_error" not in tool_result["payload"]


@pytest.mark.asyncio
async def test_tool_call_executor_traces_tool_call_and_result(tmp_path) -> None:
    trace_logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=True,
        sanitize=False,
        run_id="run-test",
    )
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        trace_logger=trace_logger,
        trace_step=2,
    )

    await executor.execute_many(
        [ToolCall(id="call-1", name="unsafe_tool", arguments={"label": "x"})]
    )

    events = [
        json.loads(line)
        for line in trace_logger.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]

    assert [event["event"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["step"] == 2
    assert events[0]["payload"] == {
        "tool": "unsafe_tool",
        "tool_call_id": "call-1",
        "args": {"label": "x"},
    }
    assert events[1]["payload"]["tool"] == "unsafe_tool"
    assert events[1]["payload"]["tool_call_id"] == "call-1"
    assert events[1]["payload"]["result"]["status"] == "success"
    assert events[1]["payload"]["result"]["text"] == "x"


@pytest.mark.asyncio
async def test_tool_call_executor_traces_hook_denied_result(tmp_path) -> None:
    trace_logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=True,
        sanitize=False,
        run_id="run-test",
    )
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(denied_tools={"unsafe_tool"}),
        session_id="session-1",
        trace_logger=trace_logger,
        trace_step=3,
    )

    await executor.execute_many(
        [ToolCall(id="call-1", name="unsafe_tool", arguments={"label": "x"})]
    )

    events = [
        json.loads(line)
        for line in trace_logger.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    result_event = events[-1]

    assert [event["event"] for event in events] == ["tool_call", "tool_result"]
    assert result_event["step"] == 3
    assert result_event["payload"]["result"]["status"] == "error"
    assert result_event["payload"]["result"]["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_tool_call_executor_journals_success_and_hook_denial(tmp_path) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")
    registry = ToolRegistry()
    registry.register(_UnsafeTool())
    registry.register(_BlockedTool())
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(denied_tools={"blocked_tool"}),
        session_id="session-1",
        operation_journal=journal,
    )

    results, _ = await executor.execute_many(
        [
            ToolCall(id="call-ok", name="unsafe_tool", arguments={"label": "ok"}),
            ToolCall(id="call-blocked", name="blocked_tool", arguments={}),
        ]
    )

    records = journal.records()
    assert [record.status for record in records] == ["committed", "failed"]
    assert records[0].outcome is not None
    assert records[0].outcome.result is not None
    assert records[1].outcome is not None
    assert records[1].outcome.error_code == "ACCESS_DENIED"
    assert results[0].envelope.context["operation_id"] == records[0].operation_id
    assert results[1].envelope.context["operation_id"] == records[1].operation_id


@pytest.mark.asyncio
async def test_unexpected_write_adapter_failure_is_journaled_unknown(tmp_path, monkeypatch) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")
    registry = ToolRegistry()
    registry.register(_UnsafeTool())

    async def allow(*args, **kwargs):
        return None

    async def explode(*args, **kwargs):
        raise RuntimeError("adapter disappeared")

    monkeypatch.setattr(registry, "check_access", allow)
    monkeypatch.setattr(registry, "invoke", explode)
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        operation_journal=journal,
    )

    results, _ = await executor.execute_many(
        [ToolCall(id="call-write", name="write_file", arguments={"path": "x"})]
    )

    assert results[0].is_error
    assert results[0].envelope.error is not None
    assert results[0].envelope.error.code == "EXECUTION_ERROR"
    assert journal.records()[0].status == "unknown"


@pytest.mark.asyncio
async def test_write_deadline_is_structured_and_journaled_unknown(tmp_path, monkeypatch) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")
    registry = ToolRegistry()

    async def allow(*args, **kwargs):
        return None

    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(registry, "check_access", allow)
    monkeypatch.setattr(registry, "invoke", hang)
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        operation_journal=journal,
        operation_timeout_s=0.01,
    )

    results, _ = await executor.execute_many(
        [ToolCall(id="call-write", name="write_file", arguments={"path": "x"})]
    )

    assert results[0].envelope.error is not None
    assert results[0].envelope.error.code == "TIMEOUT"
    assert journal.records()[0].status == "unknown"


@pytest.mark.asyncio
async def test_read_deadline_is_journaled_failed(tmp_path, monkeypatch) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")
    registry = ToolRegistry()

    async def allow(*args, **kwargs):
        return None

    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(registry, "check_access", allow)
    monkeypatch.setattr(registry, "invoke", hang)
    executor = ToolCallExecutor(
        tool_registry=registry,
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        operation_journal=journal,
        operation_timeout_s=0.01,
    )

    results, _ = await executor.execute_many(
        [ToolCall(id="call-read", name="read_file", arguments={"path": "x"})]
    )

    assert results[0].envelope.error is not None
    assert results[0].envelope.error.code == "TIMEOUT"
    assert journal.records()[0].status == "failed"


@pytest.mark.asyncio
async def test_write_operation_records_checkpoint_and_can_be_undone(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    journal = OperationJournal(tmp_path / "operations.jsonl")
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)
    executor = ToolCallExecutor(
        tool_registry=build_default_registry(),
        hook_runner=_RecordingHooks(),
        session_id="session-1",
        operation_journal=journal,
        checkpoint_store=checkpoints,
    )

    results, _ = await executor.execute_many(
        [
            ToolCall(
                id="call-write",
                name="write_file",
                arguments={"path": str(target), "content": "after\n"},
            )
        ]
    )

    operation_id = results[0].envelope.context["operation_id"]
    record = journal.get(operation_id)
    assert record.status == "committed"
    assert record.outcome is not None
    assert record.outcome.evidence["checkpoint_path"].endswith(f"{operation_id}.json")
    assert target.read_text(encoding="utf-8") == "after\n"

    checkpoints.undo(operation_id)
    assert target.read_text(encoding="utf-8") == "before\n"
