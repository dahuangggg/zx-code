from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.models import ToolEnvelope, ToolError
from agent.tools.truncator import ObservationTruncator


def _envelope(
    data_text: str = "x",
    status: str = "success",
    skip: bool = False,
) -> ToolEnvelope:
    ctx: dict = {"cwd": "."}
    if skip:
        ctx["truncation_skip"] = True
    return ToolEnvelope(
        status=status,
        data={"content": data_text},
        text="ok",
        error=(
            ToolError(code="EXECUTION_ERROR", message="failed")
            if status == "error"
            else None
        ),
        stats={"time_ms": 1},
        context=ctx,
    )


def test_truncate_accepts_and_returns_tool_envelope(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    envelope = ToolEnvelope(
        status="success",
        data={"content": "\n".join(str(i) for i in range(10))},
        text="ok",
        stats={"time_ms": 1},
        context={"cwd": "."},
    )

    result = t.truncate("read_file", envelope)

    assert isinstance(result, ToolEnvelope)
    assert result.status == "partial"
    assert result.data["truncated"] is True


def test_no_truncation_under_limits(tmp_path: Path):
    t = ObservationTruncator(tmp_path)
    envelope = _envelope("hello\nworld")
    assert t.truncate("read_file", envelope) == envelope


def test_truncation_over_line_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    envelope = _envelope(big)
    result = t.truncate("read_file", envelope)
    assert result.status == "partial"
    assert result.error is None
    assert result.data["truncated"] is True
    assert result.data["truncation"]["original_lines"] > 5
    assert "full_output_path" in result.data["truncation"]
    assert result.stats == envelope.stats
    assert result.context == envelope.context
    saved_path = tmp_path / result.data["truncation"]["full_output_path"]
    assert json.loads(saved_path.read_text(encoding="utf-8")) == json.loads(
        envelope.model_dump_json(exclude_none=True)
    )
    assert (tmp_path / "tool-output").exists()


def test_truncation_skip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    envelope = _envelope(big, skip=True)
    result = t.truncate("read_file", envelope)
    assert result == envelope


def test_error_status_preserved(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    envelope = _envelope(big, status="error")
    result = t.truncate("read_file", envelope)
    assert result.status == "error"
    assert result.error == envelope.error


def test_head_tail_direction(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    monkeypatch.setattr("agent.tools.truncator._DIRECTION", "head_tail")
    monkeypatch.setattr("agent.tools.truncator._HEAD_TAIL_LINES", 2)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(20))
    envelope = _envelope(big)
    result = t.truncate("bash", envelope)
    assert "... (truncated) ..." in result.data["preview"]


def test_non_string_content_measures_full_envelope_json(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 99999)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 50)
    t = ObservationTruncator(tmp_path)
    envelope = ToolEnvelope(
        status="success",
        data={"content": {"items": list(range(20))}},
        text="ok",
        stats={"time_ms": 1},
        context={"cwd": "."},
    )

    result = t.truncate("structured", envelope)

    assert result.status == "partial"
    assert result.data["truncation"]["original_bytes"] == len(
        envelope.model_dump_json(exclude_none=True).encode("utf-8")
    )


def test_stdout_and_stderr_are_measured_as_raw_output(tmp_path: Path, monkeypatch):
    """bash 输出截断应按 stdout/stderr 原始文本计算，而不是只看 envelope 外壳。"""
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    envelope = ToolEnvelope(
        status="success",
        data={
            "stdout": "\n".join(str(i) for i in range(100)),
            "stderr": "warning",
        },
        text="bash output",
        stats={"time_ms": 1},
        context={"cwd": "."},
    )

    result = t.truncate("bash", envelope)

    assert result.status == "partial"
    assert result.data["truncation"]["original_lines"] == 101
    assert result.data["preview"].startswith("0\n1\n2")


def test_repeated_truncations_use_unique_output_paths(tmp_path: Path, monkeypatch):
    """同一秒内多次截断也不能覆盖完整输出文件。"""
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    envelope = _envelope("\n".join(str(i) for i in range(20)))

    first = t.truncate("bash", envelope)
    second = t.truncate("bash", envelope)

    assert first.data["truncation"]["full_output_path"] != second.data["truncation"]["full_output_path"]
    assert (tmp_path / first.data["truncation"]["full_output_path"]).exists()
    assert (tmp_path / second.data["truncation"]["full_output_path"]).exists()


def test_empty_string_content_is_measured_as_content_not_full_envelope(
    tmp_path: Path,
    monkeypatch,
):
    """空字符串 content 是有效原始输出，不能退回按完整 envelope JSON 计算。"""
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 99999)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 1)
    t = ObservationTruncator(tmp_path)
    envelope = ToolEnvelope(
        status="success",
        data={"content": ""},
        text="metadata large enough to exceed byte limit if measured",
        stats={"time_ms": 1},
        context={"cwd": "."},
    )

    assert t.truncate("empty_content", envelope) == envelope
