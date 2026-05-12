from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

    async def run(self, arguments: TaskCreateInput) -> dict[str, object]:
        task = self.store.create(
            arguments.title,
            description=arguments.description,
            blocked_by=arguments.blocked_by,
        )
        return task.model_dump(mode="json")


class TaskCompleteTool(Tool):
    name = "task_complete"
    description = "Mark a DAG task completed and unlock dependents when all blockers are done."
    input_model = TaskCompleteInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskCompleteInput) -> dict[str, object]:
        task, unlocked = self.store.complete(arguments.task_id, result=arguments.result)
        return {
            "completed": task.model_dump(mode="json"),
            "unlocked": [item.model_dump(mode="json") for item in unlocked],
        }


class TaskCancelTool(Tool):
    name = "task_cancel"
    description = "Mark a DAG task as failed/cancelled and unblock dependents whose other blockers are resolved."
    input_model = TaskCancelInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskCancelInput) -> dict[str, object]:
        task, unlocked = self.store.cancel(arguments.task_id, result=arguments.result)
        return {
            "cancelled": task.model_dump(mode="json"),
            "unlocked": [item.model_dump(mode="json") for item in unlocked],
        }


class TaskUpdateTool(Tool):
    name = "task_update"
    description = "Update a DAG task status to 'pending' or 'in_progress'."
    input_model = TaskUpdateInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskUpdateInput) -> dict[str, object]:
        task = self.store.update_status(arguments.task_id, arguments.status)
        return task.model_dump(mode="json")


class TaskListTool(Tool):
    name = "task_list"
    description = "List persistent DAG tasks."
    input_model = TaskListInput

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    async def run(self, arguments: TaskListInput) -> dict[str, object]:
        tasks = self.store.list()
        if not arguments.include_completed:
            tasks = [task for task in tasks if task.status != "completed"]
        return {
            "tasks": [task.model_dump(mode="json") for task in tasks],
        }
