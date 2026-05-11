"""debuglog — optional JSONL trace logging for full agent runs."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def to_debug_json(value: Any) -> Any:
    """Convert SDK/Pydantic objects into JSON-safe debug payloads."""
    if isinstance(value, BaseModel):
        return to_debug_json(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): to_debug_json(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_debug_json(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "model_dump"):
        try:
            return to_debug_json(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return to_debug_json(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return to_debug_json(
            {
                key: item
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        )
    return repr(value)


class DebugLog:
    """Append-only debug event writer.

    Logging is best-effort: failures to serialize or write debug events must not
    break an agent run.
    """

    def __init__(self, path: Path | str, *, session_id: str | None = None) -> None:
        self.path = Path(path)
        self.session_id = session_id

    def event(
        self,
        event: str,
        payload: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": session_id or self.session_id,
            "payload": to_debug_json(payload or {}),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return
