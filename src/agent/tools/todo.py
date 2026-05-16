from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent.state.todo import TodoManager, TodoStatus
from agent.tools.base import Tool


class TodoCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    notes: str = ""


class TodoUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: str
    title: str | None = None
    status: TodoStatus | None = None
    notes: str | None = None


class TodoCompleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: str


class TodoListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TodoCreateTool(Tool):
    name = "todo_create"
    description = "Create a persistent todo item for the current session."
    input_model = TodoCreateInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoCreateInput) -> str:
        item = self.manager.create(arguments.title, notes=arguments.notes)
        return self.success(
            data=item.model_dump(mode="json"),
            text=f"Created todo '{arguments.title}' (id: {item.id}).",
        )


class TodoUpdateTool(Tool):
    name = "todo_update"
    description = "Update a persistent todo item by id."
    input_model = TodoUpdateInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoUpdateInput) -> str:
        item = self.manager.update(
            arguments.todo_id,
            title=arguments.title,
            status=arguments.status,
            notes=arguments.notes,
        )
        return self.success(
            data=item.model_dump(mode="json"),
            text=f"Updated todo {arguments.todo_id}: {item.title} [{item.status}].",
        )


class TodoCompleteTool(Tool):
    name = "todo_complete"
    description = "Mark a persistent todo item as completed."
    input_model = TodoCompleteInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoCompleteInput) -> str:
        item = self.manager.complete(arguments.todo_id)
        return self.success(
            data=item.model_dump(mode="json"),
            text=f"Completed todo: '{item.title}'.",
        )


class TodoListTool(Tool):
    name = "todo_list"
    description = "List persistent todo items for the current session."
    input_model = TodoListInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoListInput) -> str:
        items = self.manager.list()
        return self.success(
            data={"todos": [i.model_dump(mode="json") for i in items]},
            text=f"Listed {len(items)} todo item{'s' if len(items) != 1 else ''}.",
        )

