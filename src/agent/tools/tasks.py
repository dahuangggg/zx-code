from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.state.tasks import TaskStore
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
