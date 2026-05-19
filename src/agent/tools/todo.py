"""todo 工具组。

这些工具管理当前会话的轻量 todo 列表，适合模型在一次任务内维护短期步骤。
它和 state/tasks.py 的 DAG task 不同：todo 更简单，不表达依赖关系。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent.models import ToolEnvelope
from agent.state.todo import TodoManager, TodoStatus
from agent.tools.base import Tool


class TodoCreateInput(BaseModel):
    """创建 todo 的输入。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    notes: str = ""


class TodoUpdateInput(BaseModel):
    """更新 todo 的输入；未传字段保持原值。"""

    model_config = ConfigDict(extra="forbid")

    todo_id: str
    title: str | None = None
    status: TodoStatus | None = None
    notes: str | None = None


class TodoCompleteInput(BaseModel):
    """完成 todo 的输入。"""

    model_config = ConfigDict(extra="forbid")

    todo_id: str


class TodoListInput(BaseModel):
    """列出 todo 的无参数输入模型。"""

    model_config = ConfigDict(extra="forbid")


class TodoCreateTool(Tool):
    """创建当前 session 的持久 todo。"""

    name = "todo_create"
    description = "Create a persistent todo item for the current session."
    input_model = TodoCreateInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoCreateInput) -> ToolEnvelope:
        item = self.manager.create(arguments.title, notes=arguments.notes)
        return self.success(
            data=item.model_dump(mode="json"),
            text=f"Created todo '{arguments.title}' (id: {item.id}).",
        )


class TodoUpdateTool(Tool):
    """按 id 更新 todo 标题、状态或备注。"""

    name = "todo_update"
    description = "Update a persistent todo item by id."
    input_model = TodoUpdateInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoUpdateInput) -> ToolEnvelope:
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
    """按 id 标记 todo 完成。"""

    name = "todo_complete"
    description = "Mark a persistent todo item as completed."
    input_model = TodoCompleteInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoCompleteInput) -> ToolEnvelope:
        item = self.manager.complete(arguments.todo_id)
        return self.success(
            data=item.model_dump(mode="json"),
            text=f"Completed todo: '{item.title}'.",
        )


class TodoListTool(Tool):
    """列出当前 session 的 todo，data.todos 保留完整结构。"""

    name = "todo_list"
    description = "List persistent todo items for the current session."
    input_model = TodoListInput

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    async def run(self, arguments: TodoListInput) -> ToolEnvelope:
        items = self.manager.list()
        return self.success(
            data={"todos": [i.model_dump(mode="json") for i in items]},
            text=f"Listed {len(items)} todo item{'s' if len(items) != 1 else ''}.",
        )
