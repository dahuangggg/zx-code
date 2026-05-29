"""CodeContext 索引任务、取消信号和锁的协调。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


INDEX_LOCK_STALE_SECONDS = 10 * 60


class IndexCancelled(RuntimeError):
    """索引任务收到取消信号时在内部抛出。"""


class IndexJobCoordinator:
    """管理后台任务、取消事件和按代码库隔离的锁。"""

    def __init__(self, *, lock_dir: str | Path) -> None:
        self.lock_dir = Path(lock_dir).expanduser()
        self.background_tasks: dict[str, asyncio.Task[None]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.codebase_locks: dict[str, threading.RLock] = {}
        self.state_lock = threading.Lock()

    def task_for(self, codebase_id: str) -> asyncio.Task[None] | None:
        return self.background_tasks.get(codebase_id)

    def set_task(self, codebase_id: str, task: asyncio.Task[None]) -> None:
        self.background_tasks[codebase_id] = task

    def pop_task(self, codebase_id: str) -> None:
        self.background_tasks.pop(codebase_id, None)

    def lock_for(self, codebase_id: str) -> threading.RLock:
        """返回进程内按代码库隔离的锁。"""
        with self.state_lock:
            lock = self.codebase_locks.get(codebase_id)
            if lock is None:
                lock = threading.RLock()
                self.codebase_locks[codebase_id] = lock
            return lock

    def cancel_event_for(self, codebase_id: str) -> threading.Event:
        """返回按代码库隔离的取消事件。"""
        with self.state_lock:
            event = self.cancel_events.get(codebase_id)
            if event is None:
                event = threading.Event()
                self.cancel_events[codebase_id] = event
            return event

    def clear_cancel_event(self, codebase_id: str) -> None:
        self.cancel_event_for(codebase_id).clear()

    def cancel(self, codebase_id: str) -> None:
        self.cancel_event_for(codebase_id).set()

    def forget_cancel_event(self, codebase_id: str) -> None:
        self.cancel_events.pop(codebase_id, None)

    def check_cancelled(self, codebase_id: str) -> None:
        event = self.cancel_events.get(codebase_id)
        if event is not None and event.is_set():
            raise IndexCancelled()

    def index_file_lock(self, codebase_id: str) -> "_IndexFileLock":
        return _IndexFileLock(self.lock_dir / f"{codebase_id}.lock")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _IndexFileLock:
    """基于目录创建的跨进程锁；遇到过期锁会重试。"""

    def __init__(self, path: Path, *, stale_seconds: int = INDEX_LOCK_STALE_SECONDS) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.token = f"{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex}"
        self.acquired = False

    def __enter__(self) -> "_IndexFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.path.mkdir()
                (self.path / "owner.json").write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "token": self.token,
                            "acquired_at": _now(),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.acquired = True
                return self
            except FileExistsError:
                if self._is_stale():
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                raise RuntimeError(f"codebase is already being indexed: {self.path}") from None
        raise RuntimeError(f"codebase is already being indexed: {self.path}")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.acquired:
            shutil.rmtree(self.path, ignore_errors=True)
            self.acquired = False

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self.stale_seconds
        except OSError:
            return True
