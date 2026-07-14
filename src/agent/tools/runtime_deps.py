"""工具注册表构建使用的类型化依赖包。

``runtime.builder`` 填充这个 dataclass，然后 ``agent.tools`` 将其分发给各个 tool pack。
这样工具构造函数不需要依赖整个运行时对象。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.agents.subagent import SubagentRunner
from agent.agents.team import Team
from agent.agents.worktree import WorktreeManager
from agent.debuglog import DebugLog
from agent.permissions import ApprovalCallback, PermissionManager
from agent.state.memory import MemoryStore
from agent.state.skills import SkillStore
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager


@dataclass
class ToolRuntimeDeps:
    """工具包注册工具时可能需要的可选服务。"""

    permission_manager: PermissionManager | None = None
    approval_callback: ApprovalCallback | None = None
    todo_manager: TodoManager | None = None
    memory_store: MemoryStore | None = None
    skill_store: SkillStore | None = None
    task_store: TaskStore | None = None
    subagent_runner: SubagentRunner | None = None
    team: Team | None = None
    team_timeout_s: float = 30.0
    worktree_manager: WorktreeManager | None = None
    debug_log: DebugLog | None = None
