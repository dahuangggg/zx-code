"""默认工具注册入口。

build_default_registry() 根据 runtime 里实际提供的 store/runner 选择性注册
工具。基础文件和 shell 工具总是可用；todo/memory/skill/task/subagent/team/
worktree 只有依赖对象存在时才暴露。

本模块只负责编排工具 pack 的注册顺序和默认 active schema；具体工具执行逻辑
留在各自模块，依赖对象由 runtime_deps.ToolRuntimeDeps 承载。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent.tools.bash import BashTool
from agent.tools.edit import EditFileTool
from agent.tools.glob import GlobTool
from agent.tools.grep import GrepTool
from agent.tools.memory import MemoryAppendTool
from agent.tools.read import ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.runtime_deps import ToolRuntimeDeps
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

if TYPE_CHECKING:
    from agent.agents.subagent import SubagentRunner
    from agent.agents.team import Team
    from agent.agents.worktree import WorktreeManager
    from agent.debuglog import DebugLog
    from agent.permissions import ApprovalCallback, PermissionManager
    from agent.state.memory import MemoryStore
    from agent.state.skills import SkillStore
    from agent.state.tasks import TaskStore
    from agent.state.todo import TodoManager

FILE_TOOL_NAMES = ["bash", "read_file", "write_file", "edit_file", "grep", "glob"]


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
    register_file_tools(registry)
    register_state_tools(
        registry,
        todo_manager=todo_manager,
        memory_store=memory_store,
        skill_store=skill_store,
        task_store=task_store,
    )
    register_agent_tools(
        registry,
        subagent_runner=subagent_runner,
        team=team,
        team_timeout_s=team_timeout_s,
    )
    register_worktree_tools(registry, worktree_manager=worktree_manager)
    registry.register(ToolSearchTool(registry))
    registry.activate_schemas(FILE_TOOL_NAMES)
    return registry


def build_registry_from_deps(deps: ToolRuntimeDeps) -> ToolRegistry:
    """根据分组后的 runtime 依赖构建 ToolRegistry。"""
    return build_default_registry(
        permission_manager=deps.permission_manager,
        approval_callback=deps.approval_callback,
        todo_manager=deps.todo_manager,
        memory_store=deps.memory_store,
        skill_store=deps.skill_store,
        task_store=deps.task_store,
        subagent_runner=deps.subagent_runner,
        team=deps.team,
        team_timeout_s=deps.team_timeout_s,
        worktree_manager=deps.worktree_manager,
        debug_log=deps.debug_log,
    )


def register_file_tools(registry: ToolRegistry) -> None:
    """注册基础工作区工具，并作为默认 active schema 暴露给模型。"""
    registry.register(BashTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(GrepTool())
    registry.register(GlobTool())


def register_state_tools(
    registry: ToolRegistry,
    *,
    todo_manager: TodoManager | None = None,
    memory_store: MemoryStore | None = None,
    skill_store: SkillStore | None = None,
    task_store: TaskStore | None = None,
) -> None:
    """注册 Agent 状态工具：todo、memory、skill、task DAG。"""
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


def register_agent_tools(
    registry: ToolRegistry,
    *,
    subagent_runner: SubagentRunner | None = None,
    team: Team | None = None,
    team_timeout_s: float = 30.0,
) -> None:
    """注册 Agent 协作工具：subagent 与 team。"""
    if subagent_runner is not None:
        registry.register(SubagentRunTool(subagent_runner))
    if team is not None:
        registry.register(TeamListTool(team))
        registry.register(TeamDispatchTool(team, default_timeout_s=team_timeout_s))
        registry.register(TeamBroadcastTool(team))
        registry.activate_schemas(["team_list", "team_dispatch", "team_broadcast"])


def register_worktree_tools(
    registry: ToolRegistry,
    *,
    worktree_manager: WorktreeManager | None = None,
) -> None:
    """注册 Git worktree 隔离工作区工具。"""
    if worktree_manager is not None:
        registry.register(WorktreeCreateTool(worktree_manager))
        registry.register(WorktreeCleanupTool(worktree_manager))


__all__ = [
    "ToolRegistry",
    "ToolRuntimeDeps",
    "build_default_registry",
    "build_registry_from_deps",
    "register_agent_tools",
    "register_file_tools",
    "register_state_tools",
    "register_worktree_tools",
]
