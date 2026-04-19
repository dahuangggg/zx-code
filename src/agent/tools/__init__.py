from agent.tools.bash import BashTool
from agent.tools.edit_file import EditFileTool
from agent.tools.grep import GrepTool
from agent.tools.memory import MemoryAppendTool
from agent.tools.read_file import ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.subagent import SubagentRunTool
from agent.tools.todo import (
    TodoCompleteTool,
    TodoCreateTool,
    TodoListTool,
    TodoUpdateTool,
)
from agent.tools.write_file import WriteFileTool
from agent.memory import MemoryStore
from agent.permissions import ApprovalCallback, PermissionManager
from agent.subagent import SubagentRunner
from agent.todo import TodoManager


def build_default_registry(
    *,
    permission_manager: PermissionManager | None = None,
    approval_callback: ApprovalCallback | None = None,
    todo_manager: TodoManager | None = None,
    memory_store: MemoryStore | None = None,
    subagent_runner: SubagentRunner | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        permission_manager=permission_manager,
        approval_callback=approval_callback,
    )
    registry.register(BashTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(GrepTool())
    if todo_manager is not None:
        registry.register(TodoCreateTool(todo_manager))
        registry.register(TodoUpdateTool(todo_manager))
        registry.register(TodoCompleteTool(todo_manager))
        registry.register(TodoListTool(todo_manager))
    if memory_store is not None:
        registry.register(MemoryAppendTool(memory_store))
    if subagent_runner is not None:
        registry.register(SubagentRunTool(subagent_runner))
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]
