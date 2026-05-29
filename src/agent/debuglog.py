"""debuglog — 完整 agent 运行的可选 JSONL 调试日志。

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
import warnings
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel


def to_debug_json(value: Any) -> Any:
    """把 SDK/Pydantic 对象转换为 JSON-safe debug payload。"""
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
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return to_debug_json(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
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
    """只追加写入的 JSONL 调试事件记录器。

    日志是 best-effort：序列化或写入失败不能中断 agent 运行。文件句柄在首次写入时
    懒打开，并在实例生命周期内保持打开，避免反复 open/close。

    禁用日志时使用 ``DebugLog.null()`` 获取空实现。
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
        """返回空实现 DebugLog；event() 和 close() 都不做任何事。"""
        return _NullDebugLog()

    def event(
        self,
        event: str,
        payload: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
        level: str = "debug",
    ) -> None:
        """追加一条 JSONL 事件；序列化或写入失败时静默降级。"""
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
        # 尽力记录：写失败不中断 Agent 运行。
            return

    def close(self) -> None:
        """刷新并关闭底层文件句柄。"""
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    def _get_handle(self) -> IO[str]:
        """首次写入时打开并缓存文件句柄。"""
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        return self._handle


class _NullDebugLog(DebugLog):
    """禁用 debug 日志时使用的空实现。"""

    def __init__(self) -> None:
        # 不调用父类 __init__，不需要路径或文件句柄。
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
