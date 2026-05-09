"""state.sessions — 消息历史持久化，支持跨重启恢复。

``SessionStore`` 将每个 session_id 的消息历史存为 JSONL 文件
（每行一条 Message 的 JSON 序列化）。

用途：
  - agent 重启后可从上次中断的位置继续（``rebuild_messages()``）
  - 子代理使用独立 session_id，其历史与父代理完全隔离
  - 心跳、cron 各自有固定格式的 session_id，历史独立追踪

``safe_session_id()`` 将 session_id 中的非文件名字符替换为下划线，
确保文件路径合法。
"""

from __future__ import annotations


import fcntl
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import Message


def safe_session_id(session_id: str) -> str:
    normalized = session_id.strip() or "default"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized)
    return safe.strip("._") or "default"


class SessionRecord(BaseModel):
    """JSONL 文件中的单条记录。type 区分记录种类（如 "message"），payload 存具体数据。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionStore:
    """将 session 历史持久化到 JSONL 文件，每个 session_id 对应独立文件。

    写入时持文件级排他锁，防止并发追加乱序。
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{safe_session_id(session_id)}.jsonl"

    def append(self, session_id: str, record: SessionRecord | dict[str, Any]) -> None:
        parsed = (
            record
            if isinstance(record, SessionRecord)
            else SessionRecord.model_validate(record)
        )
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                handle.write(parsed.model_dump_json() + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def append_message(self, session_id: str, message: Message) -> None:
        self.append(
            session_id,
            SessionRecord(
                type="message",
                payload={"message": message.model_dump(mode="json")},
            ),
        )

    def read_records(self, session_id: str) -> list[SessionRecord]:
        path = self.path_for(session_id)
        if not path.exists():
            return []

        records: list[SessionRecord] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                    records.append(SessionRecord.model_validate(raw))
                except Exception as exc:
                    raise ValueError(f"invalid session record {path}:{line_no}") from exc
        return records

    def rebuild_messages(self, session_id: str) -> list[Message]:
        messages: list[Message] = []
        for record in self.read_records(session_id):
            if record.type != "message":
                continue
            raw_message = record.payload.get("message")
            if raw_message is None:
                continue
            messages.append(Message.model_validate(raw_message))
        return messages

