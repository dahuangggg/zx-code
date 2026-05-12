"""debuglog — optional JSONL trace logging for full agent runs.

两个实现：
  DebugLog   — 真实实现，写 JSONL 文件，每次 event() 追加一行
  NullDebugLog（通过 DebugLog.null() 获取）— 空实现，event()/close() 全部 no-op

调用方统一持有 DebugLog 类型，无需 `if debug_log is not None:` 门控。
禁用日志时传入 DebugLog.null() 即可，行为上相当于不记录任何内容。

level 字段写入每条记录，外部工具（jq、grep）可按 level 过滤，
当前 level 仅作为元数据，不在写入侧过滤（保持 best-effort 全量记录）。
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

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
    """Append-only JSONL debug event writer.

    Logging is best-effort: failures to serialize or write must not break an
    agent run. File handle is opened lazily on first write and kept open for
    the lifetime of the instance to avoid repeated open/close syscalls.

    Use ``DebugLog.null()`` to get a no-op instance when logging is disabled.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        session_id: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self._handle: IO[str] | None = None

    @classmethod
    def null(cls) -> "DebugLog":
        """Return a no-op DebugLog. event() and close() are both no-ops."""
        return _NullDebugLog()

    def event(
        self,
        event: str,
        payload: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
        level: str = "debug",
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            "session_id": session_id or self.session_id,
            "payload": to_debug_json(payload or {}),
        }
        try:
            handle = self._get_handle()
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        except Exception:
            # best-effort: 写失败不中断 Agent 运行
            return

    def close(self) -> None:
        """Flush and close the underlying file handle."""
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    def _get_handle(self) -> IO[str]:
        """Open and cache the file handle on first write."""
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        return self._handle


class _NullDebugLog(DebugLog):
    """No-op DebugLog used when debug logging is disabled."""

    def __init__(self) -> None:
        # 不调用父类 __init__，不需要 path / handle
        pass

    def event(
        self,
        event: str,
        payload: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
        level: str = "debug",
    ) -> None:
        pass

    def close(self) -> None:
        pass

    def _get_handle(self) -> IO[str]:  # type: ignore[override]
        raise NotImplementedError("NullDebugLog has no file handle")
