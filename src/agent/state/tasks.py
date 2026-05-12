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
    # 统一用 UTC ISO 8601，便于多时区环境下比较和排序
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # hex[:8] 取 32 位十六进制前 8 位，碰撞概率极低且 id 简短易读
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
        """返回所有任务，按 created_at 升序排列（时间相同时按 id 保证确定性顺序）。"""
        if not self.root.exists():
            return []
        tasks = [
            TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
        ]
        # 二级排序：created_at 保证时序，id 保证相同时间戳下顺序稳定（避免测试抖动）
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
        """创建新任务；若所有 blocker 已完成则直接设为 pending，否则设为 blocked。"""
        blockers = blocked_by or []
        # 先校验 blocker 是否存在，防止 DAG 引用悬空节点
        existing = self._ensure_blockers_exist(blockers)
        # 创建时 blocker 可能已全部完成（补录历史依赖），此时直接 pending 避免无效阻塞
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
        """更新任务状态；result 为空时保留原值（幂等更新，不清空已有结果）。"""
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
        """完成任务并自动解锁所有依赖它的下游任务，返回 (completed, unlocked_list)。"""
        completed = self.update_status(task_id, "completed", result=result)
        unlocked: list[TaskRecord] = []
        # 单次读磁盘，避免两次 list() 之间的竞争窗口
        all_tasks = self.list()
        # completed 和 failed 的上游都视为"已终结"，不阻塞下游
        resolved_ids = {
            task.id for task in all_tasks
            if task.status in ("completed", "failed")
        }
        for task in all_tasks:
            if task.status != "blocked":
                continue
            # 只有当 blocked_by 中所有上游都已终结，才解锁该任务
            if all(blocker in resolved_ids for blocker in task.blocked_by):
                updated = task.model_copy(update={"status": "pending", "updated_at": _now()})
                self._write(updated)
                unlocked.append(updated)
        return completed, unlocked

    def cancel(self, task_id: str, *, result: str = "") -> tuple[TaskRecord, list[TaskRecord]]:
        """取消任务，与 complete() 相同逻辑解锁下游（failed 也视为终结）。"""
        cancelled = self.update_status(task_id, "failed", result=result)
        unlocked: list[TaskRecord] = []
        all_tasks = self.list()
        resolved_ids = {
            task.id for task in all_tasks
            if task.status in ("completed", "failed")
        }
        for task in all_tasks:
            if task.status != "blocked":
                continue
            if all(blocker in resolved_ids for blocker in task.blocked_by):
                updated = task.model_copy(update={"status": "pending", "updated_at": _now()})
                self._write(updated)
                unlocked.append(updated)
        return cancelled, unlocked

    def ready(self) -> list[TaskRecord]:
        """返回所有依赖已满足、可立即开始的任务（status == pending）。"""
        return [task for task in self.list() if task.status == "pending"]

    def render_for_prompt(self) -> str:
        """把任务列表序列化成注入 system prompt 的纯文本；无任务时返回空串（调用方跳过注入）。"""
        tasks = self.list()
        if not tasks:
            return ""
        lines = ["Persistent DAG tasks:"]
        for task in tasks:
            blockers = f" blocked_by={','.join(task.blocked_by)}" if task.blocked_by else ""
            result = f" result={task.result!r}" if task.result and task.status in ("completed", "failed") else ""
            lines.append(f"- [{task.status}] {task.id}: {task.title}{blockers}{result}")
        return "\n".join(lines)

    def _ensure_blockers_exist(self, blockers: list[str]) -> dict[str, TaskRecord]:
        known = {task.id: task for task in self.list()}
        for blocker in blockers:
            if blocker not in known:
                raise KeyError(f"missing blocker: {blocker}")
        return known

    def _path(self, task_id: str) -> Path:
        safe = task_id.strip()
        # 禁止路径分隔符，防止 task_id 构造路径穿越（path traversal）攻击
        if not safe or "/" in safe or "\\" in safe:
            raise ValueError(f"invalid task id: {task_id}")
        return self.root / f"{safe}.json"

    def _write(self, record: TaskRecord) -> None:
        """原子写入：tempfile → fsync → rename，保证进程崩溃不产生半写文件。"""
        self.root.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,  # sort_keys 保证相同数据的 JSON 输出稳定，便于 diff 和测试
        ) + "\n"
        # mkstemp 在同一目录创建临时文件，确保 rename 是同分区操作（原子性依赖同分区）
        fd, tmp_path = tempfile.mkstemp(
            dir=self.root,
            prefix=f".tmp.{record.id}.",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())  # 强制刷到磁盘，防止断电时数据仍在 OS 缓存里
            os.replace(tmp_path, self._path(record.id))  # POSIX 原子 rename
        except BaseException:
            try:
                os.unlink(tmp_path)  # 写入失败时清理临时文件，避免 .tmp.* 垃圾堆积
            except OSError:
                pass
            raise
