from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.channels.base import ChannelManager


DeliveryStatus = Literal["queued", "sent", "failed"]

CHANNEL_TEXT_LIMITS = {
    "telegram": 4096,
    "telegram_caption": 1024,
    "feishu": 20000,
    "cli": 20000,
}


class DeliveryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    channel: str
    to: str
    text: str
    account_id: str = ""
    status: DeliveryStatus = "queued"
    retry_count: int = 0
    max_attempts: int = 5
    next_retry_at: float = 0.0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def chunk_message(channel: str, text: str, *, limit: int | None = None) -> list[str]:
    effective_limit = limit or CHANNEL_TEXT_LIMITS.get(channel, 4000)
    if effective_limit <= 0:
        raise ValueError("message chunk limit must be positive")
    if len(text) <= effective_limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= effective_limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, effective_limit + 1)
        if split_at <= 0:
            split_at = effective_limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


class DeliveryQueue:
    def __init__(
        self,
        root: Path | str,
        *,
        max_attempts: int = 5,
        base_delay_s: float = 1.0,
        max_delay_s: float = 300.0,
        jitter_s: float = 1.0,
    ) -> None:
        self.root = Path(root)
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.jitter_s = jitter_s
        self.queued_dir = self.root / "queued"
        self.sent_dir = self.root / "sent"
        self.failed_dir = self.root / "failed"
        self.ensure()

    def ensure(self) -> None:
        self.queued_dir.mkdir(parents=True, exist_ok=True)
        self.sent_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        *,
        channel: str,
        to: str,
        text: str,
        account_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryEntry:
        entry = DeliveryEntry(
            id=uuid.uuid4().hex[:12],
            channel=channel,
            to=to,
            text=text,
            account_id=account_id,
            max_attempts=self.max_attempts,
            metadata=metadata or {},
        )
        self._write_entry(self.queued_dir / f"{entry.id}.json", entry)
        return entry

    def get(self, delivery_id: str) -> DeliveryEntry | None:
        for directory in (self.queued_dir, self.sent_dir, self.failed_dir):
            path = directory / f"{delivery_id}.json"
            if path.exists():
                return self._load(path)
        return None

    def ready(self, *, now: float | None = None) -> list[DeliveryEntry]:
        current = time.time() if now is None else now
        entries = [
            entry
            for entry in self._load_many(self.queued_dir)
            if entry.next_retry_at <= current
        ]
        return sorted(entries, key=lambda item: (item.next_retry_at, item.created_at, item.id))

    def mark_sent(self, delivery_id: str) -> DeliveryEntry | None:
        entry = self.get(delivery_id)
        if entry is None:
            return None
        entry.status = "sent"
        entry.updated_at = time.time()
        final_path = self.sent_dir / f"{entry.id}.json"
        self._write_entry(final_path, entry)
        self._unlink_missing_ok(self.queued_dir / f"{entry.id}.json")
        return entry

    def mark_retry(
        self,
        delivery_id: str,
        *,
        error: str,
        now: float | None = None,
    ) -> DeliveryEntry | None:
        entry = self.get(delivery_id)
        if entry is None:
            return None
        current = time.time() if now is None else now
        entry.retry_count += 1
        entry.last_error = error
        entry.updated_at = current
        if entry.retry_count >= entry.max_attempts:
            return self._mark_failed_entry(entry, error=error, now=current)
        entry.next_retry_at = current + self._backoff_delay(entry.retry_count)
        self._write_entry(self.queued_dir / f"{entry.id}.json", entry)
        return entry

    def mark_failed(
        self,
        delivery_id: str,
        *,
        error: str,
        now: float | None = None,
    ) -> DeliveryEntry | None:
        entry = self.get(delivery_id)
        if entry is None:
            return None
        entry.status = "failed"
        entry.last_error = error
        entry.updated_at = time.time() if now is None else now
        return self._mark_failed_entry(entry, error=error, now=entry.updated_at)

    def _mark_failed_entry(
        self,
        entry: DeliveryEntry,
        *,
        error: str,
        now: float,
    ) -> DeliveryEntry:
        entry.status = "failed"
        entry.last_error = error
        entry.updated_at = now
        final_path = self.failed_dir / f"{entry.id}.json"
        self._write_entry(final_path, entry)
        self._unlink_missing_ok(self.queued_dir / f"{entry.id}.json")
        return entry

    def _backoff_delay(self, retry_count: int) -> float:
        exponential = self.base_delay_s * (2 ** max(retry_count - 1, 0))
        jitter = random.random() * self.jitter_s if self.jitter_s > 0 else 0.0
        return min(exponential + jitter, self.max_delay_s)

    def _load_many(self, directory: Path) -> list[DeliveryEntry]:
        if not directory.exists():
            return []
        entries: list[DeliveryEntry] = []
        for path in sorted(directory.glob("*.json")):
            entries.append(self._load(path))
        return entries

    def _load(self, path: Path) -> DeliveryEntry:
        return DeliveryEntry.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_entry(self, path: Path, entry: DeliveryEntry) -> None:
        payload = json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        self._atomic_write(path, payload)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".tmp.{path.stem}.",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            self._fsync_dir(path.parent)
        except BaseException:
            self._unlink_missing_ok(Path(tmp_path))
            raise

    def _fsync_dir(self, directory: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _unlink_missing_ok(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class DeliveryRunner:
    def __init__(
        self,
        *,
        queue: DeliveryQueue,
        channel_manager: ChannelManager,
    ) -> None:
        self.queue = queue
        self.channel_manager = channel_manager
        self._lock = asyncio.Lock()

    async def deliver(self, delivery_id: str) -> bool:
        async with self._lock:
            return await self._deliver_unlocked(delivery_id)

    async def _deliver_unlocked(self, delivery_id: str) -> bool:
        entry = self.queue.get(delivery_id)
        if entry is None or entry.status != "queued":
            return False
        try:
            channel = self.channel_manager.get(entry.channel)
            for chunk in chunk_message(entry.channel, entry.text):
                ok = await channel.send(
                    entry.to,
                    chunk,
                    delivery_id=entry.id,
                    **entry.metadata,
                )
                if not ok:
                    self.queue.mark_retry(entry.id, error="channel send returned false")
                    return False
        except Exception as exc:
            self.queue.mark_retry(entry.id, error=str(exc))
            return False
        self.queue.mark_sent(entry.id)
        return True

    async def deliver_ready_once(self, *, now: float | None = None) -> int:
        async with self._lock:
            delivered = 0
            for entry in self.queue.ready(now=now):
                if await self._deliver_unlocked(entry.id):
                    delivered += 1
            return delivered


class DeliveryDaemon:
    def __init__(
        self,
        *,
        runner: DeliveryRunner,
        interval_s: float = 1.0,
    ) -> None:
        self.runner = runner
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def tick(self) -> int:
        return await self.runner.deliver_ready_once()

    async def run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.interval_s)
