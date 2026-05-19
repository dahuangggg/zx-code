"""默认工具注册入口。

build_default_registry() 根据 runtime 里实际提供的 store/runner/indexer 选择性注册
工具。基础文件和 shell 工具总是可用；todo/memory/skill/task/subagent/team/
worktree/CodeContext 只有依赖对象存在时才暴露。
"""

from agent.tools.bash import BashTool
from agent.tools.code_context import (
    CodeIndexClearTool,
    CodeIndexInfoTool,
    CodeIndexListTool,
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
from agent.tools.team import TeamBroadcastTool, TeamDispatchTool, TeamListTool
from agent.tools.tasks import TaskCancelTool, TaskCompleteTool, TaskCreateTool, TaskListTool, TaskUpdateTool
from agent.tools.tool_search import ToolSearchTool
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
from agent.agents.team import Team
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
    team: Team | None = None,
    team_timeout_s: float = 30.0,
    worktree_manager: WorktreeManager | None = None,
    code_context_indexer: CodeContextIndexer | None = None,
    debug_log: DebugLog | None = None,
) -> ToolRegistry:
    """构建当前 runtime 使用的 ToolRegistry。

    这里是工具可用性的集中装配点。工具本身只描述 schema 和执行逻辑；是否注册、
    是否默认激活，以及是否携带权限/debug log，都在这里决定。
    """
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
    if team is not None:
        registry.register(TeamListTool(team))
        registry.register(TeamDispatchTool(team, default_timeout_s=team_timeout_s))
        registry.register(TeamBroadcastTool(team))
        registry.activate_schemas(["team_list", "team_dispatch", "team_broadcast"])
    if worktree_manager is not None:
        registry.register(WorktreeCreateTool(worktree_manager))
        registry.register(WorktreeCleanupTool(worktree_manager))
    if code_context_indexer is not None:
        registry.register(CodeIndexTool(code_context_indexer))
        registry.register(CodeSearchTool(code_context_indexer))
        registry.register(CodeIndexStatusTool(code_context_indexer))
        registry.register(CodeIndexListTool(code_context_indexer))
        registry.register(CodeIndexInfoTool(code_context_indexer))
        registry.register(CodeIndexClearTool(code_context_indexer))
    registry.register(ToolSearchTool(registry))
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]
