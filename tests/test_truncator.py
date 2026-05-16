from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.tools.truncator import ObservationTruncator


def _envelope(data_text: str = "x", status: str = "success", skip: bool = False) -> str:
    ctx: dict = {"cwd": "."}
    if skip:
        ctx["truncation_skip"] = True
    return json.dumps({
        "status": status,
        "data": {"content": data_text},
        "text": "ok",
        "stats": {"time_ms": 1},
        "context": ctx,
    }, ensure_ascii=False)


def test_no_truncation_under_limits(tmp_path: Path):
    t = ObservationTruncator(tmp_path)
    content = _envelope("hello\nworld")
    assert t.truncate("read_file", content) == content


def test_truncation_over_line_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    content = _envelope(big)
    result = t.truncate("read_file", content)
    env = json.loads(result)
    assert env["status"] == "partial"
    assert env["data"]["truncated"] is True
    assert env["data"]["truncation"]["original_lines"] > 5
    assert "full_output_path" in env["data"]["truncation"]
    assert (tmp_path / "tool-output").exists()


def test_truncation_skip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    content = _envelope(big, skip=True)
    result = t.truncate("read_file", content)
    assert json.loads(result)["status"] == "success"


def test_error_status_preserved(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 2)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    content = _envelope(big, status="error")
    result = t.truncate("read_file", content)
    assert json.loads(result)["status"] == "error"


def test_invalid_json_passthrough(tmp_path: Path):
    t = ObservationTruncator(tmp_path)
    raw = "not json at all"
    assert t.truncate("tool", raw) == raw


def test_head_tail_direction(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("agent.tools.truncator._MAX_LINES", 5)
    monkeypatch.setattr("agent.tools.truncator._MAX_BYTES", 99999)
    monkeypatch.setattr("agent.tools.truncator._DIRECTION", "head_tail")
    monkeypatch.setattr("agent.tools.truncator._HEAD_TAIL_LINES", 2)
    t = ObservationTruncator(tmp_path)
    big = "\n".join(str(i) for i in range(20))
    content = _envelope(big)
    result = t.truncate("bash", content)
    env = json.loads(result)
    assert "... (truncated) ..." in env["data"]["preview"]
