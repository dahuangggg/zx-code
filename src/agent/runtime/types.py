"""CLI 组合根使用的类型化运行时容器。

本模块定义从 ``runtime.builder`` 传入 ``core.loop`` 的 bundle。它只描述依赖接线形状；
服务构造留在 builder，CLI 模式编排留在 ``runtime.runner``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.agents.subagent import SubagentRunner
from agent.agents.team import Team
from agent.agents.worktree import WorktreeManager
from agent.code_context.indexer import CodeContextIndexer
from agent.config import AgentSettings
from agent.core.concurrency import LaneScheduler
from agent.core.context import ContextGuard, ModelSummaryClient
from agent.debuglog import DebugLog
from agent.hooks import HookRunner
from agent.mcp import MCPToolRouter
from agent.models import RuntimeConfig
from agent.permissions import PermissionManager
from agent.plugins import PluginManager
from agent.prompt import SystemPromptBuilder
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.state.memory import MemoryStore
from agent.state.sessions import SessionStore
from agent.state.skills import SkillStore
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager
from agent.tools.registry import ToolRegistry
from agent.trace import TraceLogger


@dataclass
class StreamOutput:
    """provider chunk 和 Markdown flush 使用的流式输出钩子。"""

    handler: Callable[[str], Any] | None = None
    renderer: MarkdownStreamRenderer | None = None

    def flush(self) -> None:
        if self.renderer is not None:
            self.renderer.flush()


@dataclass
class StateBundle:
    """单个运行时实例装配好的状态存储。"""

    memory_store: MemoryStore | None
    todo_manager: TodoManager | None
    skill_store: SkillStore | None
    task_store: TaskStore | None
    session_store: SessionStore


@dataclass
class RuntimeExtensions:
    """暴露给工具注册流程的可选扩展服务。"""

    subagent_runner: SubagentRunner | None
    team: Team | None
    worktree_manager: WorktreeManager | None
    code_context_indexer: CodeContextIndexer | None
    mcp_router: MCPToolRouter | None = None


@dataclass
class RuntimeServices:
    """prompt 刷新、工具执行和 run_task 所需的核心服务。"""

    debug_log: DebugLog
    trace_logger: TraceLogger
    permission_manager: PermissionManager
    hook_runner: HookRunner
    context_guard: ContextGuard
    model_client: Any
    summary_model_client: ModelSummaryClient
    plugin_manager: PluginManager
    prompt_builder: SystemPromptBuilder
    stream_output: StreamOutput
    lane_scheduler: LaneScheduler | None


@dataclass
class AgentRuntime:
    """单个 CLI 任务或 REPL session 的完整运行时。"""

    settings: AgentSettings
    project_root: Path
    data_dir: Path
    config: RuntimeConfig
    tool_registry: ToolRegistry
    state: StateBundle
    services: RuntimeServices
    extensions: RuntimeExtensions

    @property
    def system_prompt(self) -> str:
        return self.config.system_prompt

    @property
    def session_store(self) -> SessionStore:
        return self.state.session_store
