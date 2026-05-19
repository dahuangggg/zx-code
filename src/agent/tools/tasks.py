from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.state.tasks import TaskStore, TaskStatus
from agent.tools.base import Tool


class TaskCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    blocked_by: list[str] = Field(default_factory=list)


class TaskCompleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    result: str = ""


class TaskCancelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    result: str = ""


class TaskUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: Literal["pending", "in_progress"]


class TaskListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_completed: bool = True


class TaskCreateTool(Tool):
    name = "task_create"
    description = "Create a persistent DAG task, optionally blocked by task ids."
    input_model = TaskCreateInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskCreateInput) -> ToolEnvelope:
        task = self.store.create(
            arguments.title,
            description=arguments.description,
            blocked_by=arguments.blocked_by,
        )
        return self.success(
            data=task.model_dump(mode="json"),
            text=f"Created task '{arguments.title}' (id: {task.id}).",
        )


class TaskCompleteTool(Tool):
    name = "task_complete"
    description = "Mark a DAG task completed and unlock dependents when all blockers are done."
    input_model = TaskCompleteInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskCompleteInput) -> ToolEnvelope:
        task, unlocked = self.store.complete(arguments.task_id, result=arguments.result)
        unlocked_titles = [t.title for t in unlocked]
        unlock_note = f" Unlocked: {', '.join(unlocked_titles)}." if unlocked_titles else ""
        return self.success(
            data={"completed": task.model_dump(mode="json"), "unlocked": [t.model_dump(mode="json") for t in unlocked]},
            text=f"Completed task '{task.title}'.{unlock_note}",
        )


class TaskCancelTool(Tool):
    name = "task_cancel"
    description = "Mark a DAG task as failed/cancelled and unblock dependents whose other blockers are resolved."
    input_model = TaskCancelInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskCancelInput) -> ToolEnvelope:
        task, unlocked = self.store.cancel(arguments.task_id, result=arguments.result)
        return self.success(
            data={"cancelled": task.model_dump(mode="json"), "unlocked": [t.model_dump(mode="json") for t in unlocked]},
            text=f"Cancelled task '{task.title}'.",
        )


class TaskUpdateTool(Tool):
    name = "task_update"
    description = "Update a DAG task status to 'pending' or 'in_progress'."
    input_model = TaskUpdateInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskUpdateInput) -> ToolEnvelope:
        task = self.store.update_status(arguments.task_id, arguments.status)
        return self.success(
            data=task.model_dump(mode="json"),
            text=f"Updated task '{task.title}' → {arguments.status}.",
        )


class TaskListTool(Tool):
    name = "task_list"
    description = "List persistent DAG tasks."
    input_model = TaskListInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskListInput) -> ToolEnvelope:
        tasks = self.store.list()
        if not arguments.include_completed:
            tasks = [task for task in tasks if task.status != "completed"]
        return self.success(
            data={"tasks": [task.model_dump(mode="json") for task in tasks]},
            text=f"Listed {len(tasks)} task{'s' if len(tasks) != 1 else ''}.",
        )
