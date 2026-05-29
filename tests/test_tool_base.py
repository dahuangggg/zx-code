# tests/test_tool_base.py
from __future__ import annotations

import json
import pytest
from pydantic import BaseModel
from agent.tools.base import Tool, ErrorCode, build_error_envelope
from agent.models import ToolEnvelope, ToolError, ToolResult


class _DummyInput(BaseModel):
    x: int


class _DummyTool(Tool):
    name = "dummy"
    description = "test"
    input_model = _DummyInput

    async def run(self, arguments: _DummyInput) -> ToolEnvelope:
        return self.success(data={"value": arguments.x}, text=f"Got {arguments.x}.")


def test_success_envelope_fields():
    tool = _DummyTool()
    env = tool.success(data={"k": 1}, text="OK")
    assert isinstance(env, ToolEnvelope)
    assert env.status == "success"
    assert env.data == {"k": 1}
    assert env.text == "OK"
    assert env.stats == {}
    assert env.context["cwd"] == "."
    assert env.error is None


def test_partial_envelope_status():
    tool = _DummyTool()
    env = tool.partial(data={}, text="Partial result.")
    assert isinstance(env, ToolEnvelope)
    assert env.status == "partial"


def test_error_envelope_fields():
    tool = _DummyTool()
    env = tool.error(code=ErrorCode.NOT_FOUND, message="Missing")
    assert isinstance(env, ToolEnvelope)
    assert env.status == "error"
    assert env.error is not None
    assert env.error.code == "NOT_FOUND"
    assert env.error.message == "Missing"


def test_build_error_envelope_standalone():
    raw = build_error_envelope(ErrorCode.CIRCUIT_OPEN, "Blocked")
    env = json.loads(raw)
    assert env["status"] == "error"
    assert env["error"]["code"] == "CIRCUIT_OPEN"


def test_tool_result_serializes_envelope_as_tool_message_content():
    result = ToolResult(
        call_id="call-1",
        name="dummy",
        envelope=ToolEnvelope(
            status="success",
            data={"value": 7},
            text="done",
            stats={"time_ms": 3},
            context={"params_input": {"x": 7}},
        ),
    )

    message = result.to_message()
    content = json.loads(message.content)

    assert message.role == "tool"
    assert message.tool_call_id == "call-1"
    assert message.name == "dummy"
    assert content["status"] == "success"
    assert content["data"] == {"value": 7}
    assert content["text"] == "done"
    assert result.status == "success"
    assert result.is_error is False


def test_tool_result_model_copy_normalizes_compat_content_and_status_update():
    """保护兼容截断路径：model_copy(update=content/status) 不能生成影子字段。"""
    result = ToolResult(
        call_id="call-1",
        name="dummy",
        envelope=ToolEnvelope(status="success", data={"value": 7}, text="full"),
    )
    truncated_content = json.dumps(
        {
            "status": "partial",
            "data": {"truncated": True},
            "text": "preview",
            "stats": {"time_ms": 4},
            "context": {"full_output_path": ".zx-code/artifacts/tool-output/call-1.txt"},
        }
    )

    copied = result.model_copy(
        update={"content": truncated_content, "status": "partial", "is_error": False}
    )
    copied_content = json.loads(copied.content)

    assert copied.status == "partial"
    assert copied.is_error is False
    assert copied_content["status"] == "partial"
    assert copied_content["data"] == {"truncated": True}
    assert copied_content["text"] == "preview"
    assert "content" not in copied.__dict__
    assert "status" not in copied.__dict__
    assert "is_error" not in copied.__dict__


def test_tool_result_model_copy_normalizes_compat_status_only_update():
    result = ToolResult(
        call_id="call-1",
        name="dummy",
        envelope=ToolEnvelope(status="success", data={"value": 7}, text="full"),
    )

    copied = result.model_copy(update={"status": "partial"})

    assert copied.status == "partial"
    assert json.loads(copied.content)["status"] == "partial"
    assert "status" not in copied.__dict__


def test_tool_result_wraps_arbitrary_compat_dict_content_as_raw_data():
    """保护插件/MCP 兼容路径：任意 dict 不是 ToolEnvelope 协议时必须包进 data.raw。"""
    result = ToolResult(
        call_id="call-1",
        name="dummy",
        content={"foo": "bar", "count": 2},
    )

    assert result.status == "success"
    assert result.envelope.data == {"raw": {"foo": "bar", "count": 2}}
    assert result.envelope.text == json.dumps({"foo": "bar", "count": 2}, ensure_ascii=False)


def test_tool_result_wraps_arbitrary_compat_json_object_content_as_raw_data():
    raw = json.dumps({"foo": "bar", "count": 2}, ensure_ascii=False)

    result = ToolResult(
        call_id="call-1",
        name="dummy",
        content=raw,
    )

    assert result.status == "success"
    assert result.envelope.data == {"raw": {"foo": "bar", "count": 2}}
    assert result.envelope.text == raw


def test_tool_envelope_requires_error_payload_for_error_status():
    with pytest.raises(ValueError, match="error payload"):
        ToolEnvelope(status="error")


def test_tool_envelope_rejects_error_payload_for_success_status():
    with pytest.raises(ValueError, match="non-error"):
        ToolEnvelope(
            status="success",
            error=ToolError(code="INTERNAL_ERROR", message="boom"),
        )


def test_extra_context_propagated():
    tool = _DummyTool()
    env = tool.success(data={}, text="ok", extra_context={"file_mtime_ms": 123})
    assert env.context["cwd"] == "."
    assert env.context["file_mtime_ms"] == 123


@pytest.mark.asyncio
async def test_execute_injects_time_ms_and_params():
    tool = _DummyTool()
    result = await tool.execute({"x": 5}, call_id="c1")
    assert not result.is_error
    assert result.status == "success"
    assert result.envelope.stats["time_ms"] >= 0
    assert result.envelope.context["cwd"] == "."
    assert result.envelope.context["params_input"] == {"x": 5}
    assert result.envelope.data["value"] == 5


@pytest.mark.asyncio
async def test_execute_normalizes_compat_string_return_to_envelope():
    class _CompatStringTool(Tool):
        name = "compat_string"
        description = "test"
        input_model = _DummyInput

        async def run(self, arguments: _DummyInput) -> ToolEnvelope:
            return f"compat {arguments.x}"

    result = await _CompatStringTool().execute({"x": 8}, call_id="compat-1")

    assert result.status == "success"
    assert result.envelope.text == "compat 8"
    assert result.envelope.stats["time_ms"] >= 0
    assert result.envelope.context["cwd"] == "."
    assert result.envelope.context["params_input"] == {"x": 8}


@pytest.mark.asyncio
async def test_execute_normalizes_compat_dict_return_to_envelope():
    class _CompatDictTool(Tool):
        name = "compat_dict"
        description = "test"
        input_model = _DummyInput

        async def run(self, arguments: _DummyInput) -> ToolEnvelope:
            return {"status": "partial", "data": {"value": arguments.x}, "text": "compat"}

    result = await _CompatDictTool().execute({"x": 9}, call_id="compat-2")

    assert result.status == "partial"
    assert result.envelope.data == {"value": 9}
    assert result.envelope.text == "compat"
    assert result.envelope.stats["time_ms"] >= 0
    assert result.envelope.context["params_input"] == {"x": 9}


@pytest.mark.asyncio
async def test_execute_returns_invalid_param_on_bad_args():
    tool = _DummyTool()
    result = await tool.execute({"x": "not-an-int"}, call_id="c2")
    assert result.is_error
    assert result.status == "error"
    assert result.envelope.error is not None
    assert result.envelope.error.code == "INVALID_PARAM"
    assert result.envelope.stats["time_ms"] >= 0
    assert result.envelope.context["cwd"] == "."
    assert result.envelope.context["params_input"] == {"x": "not-an-int"}


@pytest.mark.asyncio
async def test_execute_returns_internal_error_on_exception():
    class _BrokenTool(Tool):
        name = "broken"
        description = "test"
        input_model = _DummyInput

        async def run(self, arguments: _DummyInput) -> ToolEnvelope:
            raise RuntimeError("boom")

    result = await _BrokenTool().execute({"x": 1}, call_id="c3")
    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "INTERNAL_ERROR"
    assert "boom" in result.envelope.error.message
    assert result.envelope.stats["time_ms"] >= 0
    assert result.envelope.context["cwd"] == "."
    assert result.envelope.context["params_input"] == {"x": 1}
