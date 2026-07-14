from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.models import ToolEnvelope, ToolResult
from agent.operations import (
    OperationJournal,
    OperationJournalCorruption,
    OperationRequest,
    OperationTransitionError,
)


def _request(operation_id: str = "op-1") -> OperationRequest:
    return OperationRequest(
        operation_id=operation_id,
        session_id="session-1",
        call_id="call-1",
        tool_name="write_file",
        arguments={"path": "README.md", "content": "hello"},
        capability="write_fs",
        side_effect="write",
    )


def _result() -> ToolResult:
    return ToolResult(
        call_id="call-1",
        name="write_file",
        envelope=ToolEnvelope(status="success", text="wrote README.md"),
    )


def test_journal_projects_started_and_committed_operation(tmp_path: Path) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")

    journal.start(_request())
    assert journal.get("op-1").status == "started"

    journal.commit("op-1", result=_result(), evidence={"diff": "README.md"})
    record = journal.get("op-1")

    assert record.status == "committed"
    assert record.request.tool_name == "write_file"
    assert record.outcome is not None
    assert record.outcome.result == _result()
    assert record.outcome.evidence == {"diff": "README.md"}
    assert journal.pending() == ()


def test_journal_recovery_marks_incomplete_operations_unknown(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationJournal(path)
    journal.start(_request("op-a"))
    journal.start(_request("op-b"))
    journal.fail("op-b", error_code="ACCESS_DENIED", message="blocked")

    recovered = OperationJournal(path).recover_incomplete(
        reason="process exited before terminal record"
    )

    assert [record.operation_id for record in recovered] == ["op-a"]
    assert recovered[0].status == "unknown"
    assert recovered[0].outcome is not None
    assert recovered[0].outcome.message == "process exited before terminal record"
    assert OperationJournal(path).get("op-b").status == "failed"


def test_journal_rejects_duplicate_or_terminal_transitions(tmp_path: Path) -> None:
    journal = OperationJournal(tmp_path / "operations.jsonl")
    journal.start(_request())

    with pytest.raises(OperationTransitionError):
        journal.start(_request())

    journal.commit("op-1", result=_result())
    with pytest.raises(OperationTransitionError):
        journal.fail("op-1", error_code="LATE", message="too late")


def test_journal_ignores_only_a_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationJournal(path)
    journal.start(_request())
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"operation_id":"partial"')

    assert OperationJournal(path).get("op-1").status == "started"

    path.write_text('{broken}\n' + path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(OperationJournalCorruption):
        OperationJournal(path).pending()


def test_journal_redacts_secret_arguments_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "operations.jsonl"
    journal = OperationJournal(path)
    request = _request().model_copy(
        update={
            "arguments": {
                "token": "super-secret",
                "command": "curl -H 'Authorization: Bearer abc.def' example.test",
            }
        }
    )

    journal.start(request)

    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert "super-secret" not in raw
    assert "abc.def" not in raw
    assert event["request"]["arguments"]["token"] == "<redacted:token>"
