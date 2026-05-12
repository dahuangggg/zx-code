from agent.tools.bash import BashTool
from agent.tools.code_context import (
    CodeIndexClearTool,
    CodeIndexStatusTool,
    CodeIndexTool,
    CodeSearchTool,
)
from agent.tools.edit import EditFileTool
from agent.tools.grep import GrepTool
from agent.tools.memory import MemoryAppendTool
from agent.tools.read import ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.skill import LoadSkillTool
from agent.tools.subagent import SubagentRunTool
from agent.tools.tasks import TaskCancelTool, TaskCompleteTool, TaskCreateTool, TaskListTool, TaskUpdateTool
from agent.tools.todo import (
    TodoCompleteTool,
    TodoCreateTool,
    TodoListTool,
    TodoUpdateTool,
)
from agent.tools.write import WriteFileTool
from agent.tools.worktree import WorktreeCleanupTool, WorktreeCreateTool
from agent.state.memory import MemoryStore
from agent.permissions import ApprovalCallback, PermissionManager
from agent.state.skills import SkillStore
from agent.agents.subagent import SubagentRunner
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager
from agent.agents.worktree import WorktreeManager
from agent.code_context.indexer import CodeContextIndexer
from agent.debuglog import DebugLog


def build_default_registry(
    *,
    permission_manager: PermissionManager | None = None,
    approval_callback: ApprovalCallback | None = None,
    todo_manager: TodoManager | None = None,
    memory_store: MemoryStore | None = None,
    skill_store: SkillStore | None = None,
    task_store: TaskStore | None = None,
    subagent_runner: SubagentRunner | None = None,
    worktree_manager: WorktreeManager | None = None,
    code_context_indexer: CodeContextIndexer | None = None,
    debug_log: DebugLog | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        permission_manager=permission_manager,
        approval_callback=approval_callback,
        debug_log=debug_log,
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
    if skill_store is not None:
        registry.register(LoadSkillTool(skill_store))
    if task_store is not None:
        registry.register(TaskCreateTool(task_store))
        registry.register(TaskCompleteTool(task_store))
        registry.register(TaskCancelTool(task_store))
        registry.register(TaskUpdateTool(task_store))
        registry.register(TaskListTool(task_store))
    if subagent_runner is not None:
        registry.register(SubagentRunTool(subagent_runner))
    if worktree_manager is not None:
        registry.register(WorktreeCreateTool(worktree_manager))
        registry.register(WorktreeCleanupTool(worktree_manager))
    if code_context_indexer is not None:
        registry.register(CodeIndexTool(code_context_indexer))
        registry.register(CodeSearchTool(code_context_indexer))
        registry.register(CodeIndexStatusTool(code_context_indexer))
        registry.register(CodeIndexClearTool(code_context_indexer))
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]
