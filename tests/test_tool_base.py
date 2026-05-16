# tests/test_tool_base.py
from __future__ import annotations

import json
import pytest
from pydantic import BaseModel
from agent.tools.base import Tool, ErrorCode, build_error_envelope
from agent.models import ToolResult


class _DummyInput(BaseModel):
    x: int


class _DummyTool(Tool):
    name = "dummy"
    description = "test"
    input_model = _DummyInput

    async def run(self, arguments: _DummyInput) -> str:
        return self.success(data={"value": arguments.x}, text=f"Got {arguments.x}.")


def test_success_envelope_fields():
    tool = _DummyTool()
    raw = tool.success(data={"k": 1}, text="OK")
    env = json.loads(raw)
    assert env["status"] == "success"
    assert env["data"] == {"k": 1}
    assert env["text"] == "OK"
    assert "stats" in env
    assert "context" in env
    assert "error" not in env


def test_partial_envelope_status():
    tool = _DummyTool()
    env = json.loads(tool.partial(data={}, text="Partial result."))
    assert env["status"] == "partial"


def test_error_envelope_fields():
    tool = _DummyTool()
    env = json.loads(tool.error(code=ErrorCode.NOT_FOUND, message="Missing"))
    assert env["status"] == "error"
    assert env["error"]["code"] == "NOT_FOUND"
    assert env["error"]["message"] == "Missing"


def test_build_error_envelope_standalone():
    raw = build_error_envelope(ErrorCode.CIRCUIT_OPEN, "Blocked")
    env = json.loads(raw)
    assert env["status"] == "error"
    assert env["error"]["code"] == "CIRCUIT_OPEN"


def test_extra_context_propagated():
    tool = _DummyTool()
    env = json.loads(tool.success(data={}, text="ok", extra_context={"file_mtime_ms": 123}))
    assert env["context"]["file_mtime_ms"] == 123


@pytest.mark.asyncio
async def test_execute_injects_time_ms_and_params():
    tool = _DummyTool()
    result = await tool.execute({"x": 5}, call_id="c1")
    assert not result.is_error
    assert result.status == "success"
    env = json.loads(result.content)
    assert env["stats"]["time_ms"] >= 0
    assert env["context"]["params_input"] == {"x": 5}
    assert env["data"]["value"] == 5


@pytest.mark.asyncio
async def test_execute_returns_invalid_param_on_bad_args():
    tool = _DummyTool()
    result = await tool.execute({"x": "not-an-int"}, call_id="c2")
    assert result.is_error
    assert result.status == "error"
    env = json.loads(result.content)
    assert env["error"]["code"] == "INVALID_PARAM"


@pytest.mark.asyncio
async def test_execute_returns_internal_error_on_exception():
    class _BrokenTool(Tool):
        name = "broken"
        description = "test"
        input_model = _DummyInput

        async def run(self, arguments: _DummyInput) -> str:
            raise RuntimeError("boom")

    result = await _BrokenTool().execute({"x": 1}, call_id="c3")
    assert result.is_error
    env = json.loads(result.content)
    assert env["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" in env["error"]["message"]
