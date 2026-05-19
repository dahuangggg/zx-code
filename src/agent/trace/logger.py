"""JSONL trace logger with stable audit/replay event shape."""
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.debuglog import to_debug_json
from agent.trace.html import write_trace_html
from agent.trace.sanitizer import TraceSanitizer

TRACE_SCHEMA_VERSION = "trace.v1"


def new_run_id() -> str:
    """Generate a readable run id for one agent invocation."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:8]}"


class TraceLogger:
    """Stable JSONL audit logger, separate from best-effort DebugLog."""

    def __init__(
        self,
        *,
        session_id: str,
        trace_dir: Path | str,
        enabled: bool = True,
        sanitize: bool = True,
        html_enabled: bool = False,
        html_include_raw_response: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.trace_dir = Path(trace_dir).expanduser()
        self.enabled = enabled
        self.sanitize = sanitize
        self.html_enabled = html_enabled
        self.html_include_raw_response = html_include_raw_response
        self.run_id = run_id or new_run_id()
        self.jsonl_path = self.trace_dir / f"{self.run_id}.jsonl"
        self.html_path = self.trace_dir / f"{self.run_id}.html"
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._closed = False
        self._sanitizer = TraceSanitizer()

    @classmethod
    def null(cls) -> "TraceLogger":
        return cls(session_id="", trace_dir=".", enabled=False)

    def log_event(
        self,
        event: str,
        payload: Mapping[str, Any] | None = None,
        *,
        step: int = 0,
    ) -> None:
        """Append one JSONL event if tracing is enabled."""
        if not self.enabled or self._closed:
            return
        raw_payload = to_debug_json(payload or {})
        safe_payload = self._sanitizer.sanitize(raw_payload) if self.sanitize else raw_payload
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "step": step,
            "event": event,
            "payload": safe_payload,
        }
        with self._lock:
            try:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                with self.jsonl_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                self._events.append(record)
            except Exception:
                return

    def finalize(self, summary: Mapping[str, Any] | None = None) -> None:
        """Write terminal events and optional HTML exactly once."""
        if self._closed:
            return
        if summary is not None:
            self.log_event("session_summary", summary, step=0)
        self.log_event("run_end", {}, step=0)
        if self.enabled and self.html_enabled:
            try:
                self.trace_dir.mkdir(parents=True, exist_ok=True)
                write_trace_html(self.html_path, self._events)
            except Exception:
                pass
        self._closed = True

    def close(self) -> None:
        self.finalize()
