"""Crash-aware append-only operation journal。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import ValidationError

from agent.models import ToolResult
from agent.operations.models import (
    JournalEvent,
    OperationOutcome,
    OperationRecord,
    OperationRequest,
)
from agent.trace.sanitizer import TraceSanitizer

try:
    import fcntl
except ImportError:  # pragma: no cover - current CLI targets Unix
    fcntl = None  # type: ignore[assignment]


class OperationTransitionError(RuntimeError):
    pass


class OperationJournalCorruption(RuntimeError):
    pass


class OperationJournal:
    """以 JSONL event 重建 operation 状态，写入时 flush + fsync。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self._sanitizer = TraceSanitizer()

    def start(self, request: OperationRequest) -> OperationRecord:
        records = self._records()
        if request.operation_id in records:
            raise OperationTransitionError(
                f"operation already exists: {request.operation_id}"
            )
        sanitized = request.model_copy(
            update={"arguments": self._sanitizer.sanitize(request.arguments)}
        )
        event = JournalEvent(
            operation_id=request.operation_id,
            status="started",
            request=sanitized,
        )
        self._append(event)
        return self.get(request.operation_id)

    def commit(
        self,
        operation_id: str,
        *,
        result: ToolResult,
        evidence: dict[str, Any] | None = None,
    ) -> OperationRecord:
        sanitized_result = ToolResult.model_validate(
            self._sanitizer.sanitize(result.model_dump(mode="json"))
        )
        return self._finish(
            operation_id,
            OperationOutcome(
                status="committed",
                result=sanitized_result,
                evidence=self._sanitizer.sanitize(evidence or {}),
            ),
        )

    def fail(
        self,
        operation_id: str,
        *,
        error_code: str,
        message: str,
        result: ToolResult | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> OperationRecord:
        sanitized_result = None
        if result is not None:
            sanitized_result = ToolResult.model_validate(
                self._sanitizer.sanitize(result.model_dump(mode="json"))
            )
        return self._finish(
            operation_id,
            OperationOutcome(
                status="failed",
                result=sanitized_result,
                error_code=error_code,
                message=self._sanitizer.sanitize(message),
                evidence=self._sanitizer.sanitize(evidence or {}),
            ),
        )

    def mark_unknown(self, operation_id: str, *, reason: str) -> OperationRecord:
        return self._finish(
            operation_id,
            OperationOutcome(
                status="unknown",
                message=self._sanitizer.sanitize(reason),
            ),
        )

    def get(self, operation_id: str) -> OperationRecord:
        try:
            return self._records()[operation_id]
        except KeyError as exc:
            raise KeyError(f"unknown operation: {operation_id}") from exc

    def pending(self) -> tuple[OperationRecord, ...]:
        return tuple(
            record for record in self._records().values() if record.status == "started"
        )

    def records(self) -> tuple[OperationRecord, ...]:
        return tuple(self._records().values())

    def recover_incomplete(self, *, reason: str) -> tuple[OperationRecord, ...]:
        recovered: list[OperationRecord] = []
        for record in self.pending():
            recovered.append(self.mark_unknown(record.operation_id, reason=reason))
        return tuple(recovered)

    def _finish(
        self,
        operation_id: str,
        outcome: OperationOutcome,
    ) -> OperationRecord:
        record = self.get(operation_id)
        if record.status != "started":
            raise OperationTransitionError(
                f"operation {operation_id} is already {record.status}"
            )
        self._append(
            JournalEvent(
                operation_id=operation_id,
                status=outcome.status,
                outcome=outcome,
            )
        )
        return self.get(operation_id)

    def _records(self) -> dict[str, OperationRecord]:
        records: dict[str, OperationRecord] = {}
        for event in self._events():
            existing = records.get(event.operation_id)
            if event.status == "started":
                if existing is not None or event.request is None:
                    raise OperationJournalCorruption(
                        f"invalid started transition: {event.operation_id}"
                    )
                records[event.operation_id] = OperationRecord(
                    operation_id=event.operation_id,
                    status="started",
                    request=event.request,
                    started_at=event.timestamp,
                )
                continue
            if existing is None or existing.status != "started" or event.outcome is None:
                raise OperationJournalCorruption(
                    f"invalid terminal transition: {event.operation_id}"
                )
            records[event.operation_id] = existing.model_copy(
                update={"status": event.status, "outcome": event.outcome}
            )
        return records

    def _events(self) -> list[JournalEvent]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        lines = raw.splitlines()
        events: list[JournalEvent] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                events.append(JournalEvent.model_validate_json(line))
            except (ValidationError, ValueError) as exc:
                is_truncated_tail = index == len(lines) - 1 and not raw.endswith(b"\n")
                if is_truncated_tail:
                    break
                raise OperationJournalCorruption(
                    f"invalid journal event at line {index + 1}"
                ) from exc
        return events

    def _append(self, event: JournalEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = event.model_dump_json().encode("utf-8") + b"\n"
        with self._lock:
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
