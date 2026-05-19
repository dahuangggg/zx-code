from __future__ import annotations

import json
from pathlib import Path

from agent.trace import TraceLogger


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_trace_logger_writes_standard_event_shape(tmp_path: Path) -> None:
    logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=True,
        sanitize=False,
        html_enabled=False,
        run_id="run-test",
    )

    logger.log_event("user_input", {"text": "hello"}, step=1)

    events = _read_events(logger.jsonl_path)
    assert len(events) == 1
    assert events[0]["schema_version"] == "trace.v1"
    assert events[0]["session_id"] == "session-1"
    assert events[0]["run_id"] == "run-test"
    assert events[0]["step"] == 1
    assert events[0]["event"] == "user_input"
    assert events[0]["payload"] == {"text": "hello"}
    assert events[0]["ts"]


def test_disabled_trace_logger_is_noop(tmp_path: Path) -> None:
    logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=False,
        run_id="run-test",
    )

    logger.log_event("user_input", {"text": "hello"}, step=1)
    logger.finalize({"total_turns": 1})

    assert not logger.jsonl_path.exists()
    assert not logger.html_path.exists()


def test_trace_logger_finalize_is_idempotent(tmp_path: Path) -> None:
    logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=True,
        sanitize=False,
        run_id="run-test",
    )

    logger.finalize({"total_turns": 1})
    logger.finalize({"total_turns": 2})

    events = _read_events(logger.jsonl_path)
    assert [event["event"] for event in events] == ["session_summary", "run_end"]
    assert events[0]["payload"] == {"total_turns": 1}


def test_trace_logger_writes_html_report_when_enabled(tmp_path: Path) -> None:
    logger = TraceLogger(
        session_id="session-1",
        trace_dir=tmp_path,
        enabled=True,
        sanitize=False,
        html_enabled=True,
        run_id="run-test",
    )

    logger.log_event("model_output", {"text": "<script>alert(1)</script>"}, step=1)
    logger.finalize()

    html = logger.html_path.read_text(encoding="utf-8")
    assert "model_output" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

