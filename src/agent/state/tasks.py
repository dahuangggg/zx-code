"""state.tasks — 文件持久化的 DAG 任务编排（s12）。

``TaskStore`` 实现类似 Claude Code TodoWrite 的任务系统，但额外支持依赖关系：

每个任务存为独立 JSON 文件（.tasks/<id>.json），包含：
  - id / title / status（pending / in_progress / completed / cancelled）
  - blocked_by：依赖的上游任务 id 列表

DAG 语义：
  - ``complete()`` 完成任务时，自动扫描并解锁（unblock）所有依赖它的下游任务
  - ``list_ready()`` 返回所有依赖已满足、可立即开始的任务

持久化：原子写入（tempfile → fsync → rename），防止进程崩溃损坏文件。
"""

from __future__ import annotations


import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


TaskStatus = Literal["pending", "blocked", "in_progress", "completed", "failed"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:8]}")
    title: str
    description: str = ""
    status: TaskStatus = "pending"
    blocked_by: list[str] = Field(default_factory=list)
    result: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TaskStore:
    """JSON-file DAG task store.

    Each task is one file so task state survives compaction, process restarts,
    and child-agent handoffs while remaining easy to inspect by hand.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def list(self) -> list[TaskRecord]:
        if not self.root.exists():
            return []
        tasks = [
            TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
        ]
        return sorted(tasks, key=lambda item: (item.created_at, item.id))

    def get(self, task_id: str) -> TaskRecord:
        path = self._path(task_id)
        if not path.exists():
            raise KeyError(f"task not found: {task_id}")
        return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def create(
        self,
        title: str,
        *,
        description: str = "",
        blocked_by: list[str] | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        blockers = blocked_by or []
        existing = self._ensure_blockers_exist(blockers)
        blockers_completed = all(
            existing[blocker].status == "completed"
            for blocker in blockers
        )
        record = TaskRecord(
            id=task_id or f"task-{uuid4().hex[:8]}",
            title=title,
            description=description,
            blocked_by=blockers,
            status="blocked" if blockers and not blockers_completed else "pending",
        )
        if self._path(record.id).exists():
            raise ValueError(f"task already exists: {record.id}")
        self._write(record)
        return record

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: str = "",
    ) -> TaskRecord:
        record = self.get(task_id)
        updated = record.model_copy(
            update={
                "status": status,
                "result": result if result else record.result,
                "updated_at": _now(),
            }
        )
        self._write(updated)
        return updated

    def complete(self, task_id: str, *, result: str = "") -> tuple[TaskRecord, list[TaskRecord]]:
        completed = self.update_status(task_id, "completed", result=result)
        unlocked: list[TaskRecord] = []
        completed_ids = {task.id for task in self.list() if task.status == "completed"}
        for task in self.list():
            if task.status != "blocked":
                continue
            if all(blocker in completed_ids for blocker in task.blocked_by):
                updated = task.model_copy(update={"status": "pending", "updated_at": _now()})
                self._write(updated)
                unlocked.append(updated)
        return completed, unlocked

    def ready(self) -> list[TaskRecord]:
        return [task for task in self.list() if task.status == "pending"]

    def render_for_prompt(self) -> str:
        tasks = self.list()
        if not tasks:
            return ""
        lines = ["Persistent DAG tasks:"]
        for task in tasks:
            blockers = f" blocked_by={','.join(task.blocked_by)}" if task.blocked_by else ""
            lines.append(f"- [{task.status}] {task.id}: {task.title}{blockers}")
        return "\n".join(lines)

    def _ensure_blockers_exist(self, blockers: list[str]) -> dict[str, TaskRecord]:
        known = {task.id: task for task in self.list()}
        for blocker in blockers:
            if blocker not in known:
                raise KeyError(f"missing blocker: {blocker}")
        return known

    def _path(self, task_id: str) -> Path:
        safe = task_id.strip()
        if not safe or "/" in safe or "\\" in safe:
            raise ValueError(f"invalid task id: {task_id}")
        return self.root / f"{safe}.json"

    def _write(self, record: TaskRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            dir=self.root,
            prefix=f".tmp.{record.id}.",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path(record.id))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
