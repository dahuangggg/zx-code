"""runtime.builder — CLI Agent 运行时组合根。

本模块负责依赖构造：state stores、prompt builder、permissions、tools、model clients、
hooks、trace、MCP 和 typed AgentRuntime。它不选择 CLI 模式，也不直接运行 ReAct loop；
runner.py 选择模式，core.loop 消费组装好的运行时服务。
"""

from __future__ import annotations


from pathlib import Path
from typing import Any, Callable

from agent.config import AgentSettings
from agent.core.context import ContextGuard, ModelSummaryClient
from agent.debuglog import DebugLog
from agent.errors import AgentError
from agent.hooks import HookRunner
from agent.core.concurrency import LaneScheduler
from agent.core.loop import run_task
from agent.mcp import MCPToolRouter, StdioMCPClient
from agent.state.memory import MemoryStore
from agent.permissions import PermissionManager
from agent.plugins import PluginManager
from agent.profiles import FallbackModelClient
from agent.prompt import SystemPromptBuilder, TOOL_DISPLAY_NAMES
from agent.providers.openai_client import OpenAIModelClient
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.types import (
    AgentRuntime,
    RuntimeExtensions,
    RuntimeServices,
    StateBundle,
    StreamOutput,
)
from agent.runtime.utils import _approval_prompt, _resolve_project_path, _stream_printer, console
from agent.state.sessions import SessionStore, safe_session_id
from agent.trace import TraceLogger
from agent.state.skills import SkillStore
from agent.agents.subagent import SubagentRunner
from agent.agents.team import Team
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager
from agent.tools import ToolRuntimeDeps, build_registry_from_deps
from agent.agents.worktree import WorktreeManager
from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.embeddings import OpenAICodeEmbeddingFunction
from agent.code_context.indexer import CodeContextIndexer


class CLIProgressReporter:
    """把 run_task 的 progress 事件翻译成 CLI 可见状态。

    模型输出流式展示时，用户需要看到 thinking/tool start/tool end，否则长任务看起来
    像卡住。这个类只负责展示，不参与业务控制流。
    """

    def __init__(
        self,
        output_console: Any,
        *,
        flush_output: Callable[[], Any] | None = None,
    ) -> None:
        self.console = output_console
        self.flush_output = flush_output
        self._status: Any | None = None

    def handle(self, event: str, payload: dict[str, Any]) -> None:
        if event == "model.start":
            self._start_thinking()
            return
        if event in {"model.chunk", "model.end"}:
            self.stop()
            return
        if event == "tool.start":
            self.stop()
            if self.flush_output is not None:
                self.flush_output()
            tool_name = str(payload.get("tool_name") or "tool")
            arguments = payload.get("arguments")
            summary = _summarize_tool_arguments(arguments)
            suffix = f" [dim]{summary}[/dim]" if summary else ""
            display_name = _display_tool_name(tool_name)
            self.console.print(f"[dim]tool[/dim] [cyan]{display_name}[/cyan]{suffix}")
            return
        if event == "tool.end":
            tool_name = str(payload.get("tool_name") or "tool")
            is_error = bool(payload.get("is_error"))
            result_text = str(payload.get("result_text") or "").strip()
            first_line = result_text.split("\n")[0] if result_text else ""
            if first_line:
                label = f"[red]{first_line}[/red]" if is_error else f"[dim]{first_line}[/dim]"
            else:
                label = "[red]failed[/red]" if is_error else "[green]done[/green]"
            display_name = _display_tool_name(tool_name)
            self.console.print(f"[dim]tool[/dim] [cyan]{display_name}[/cyan] {label}")

    def stop(self) -> None:
        if self._status is None:
            return
        self._status.stop()
        self._status = None

    def _start_thinking(self) -> None:
        self.stop()
        self._status = self.console.status("[cyan]thinking[/cyan]", spinner="dots")
        self._status.start()


def _summarize_tool_arguments(arguments: Any) -> str:
    """为 CLI 工具提示挑一个最有信息量的参数做短摘要。"""
    if not isinstance(arguments, dict) or not arguments:
        return ""
    for key in ("command", "path", "file_path", "pattern", "name", "label"):
        value = arguments.get(key)
        if value:
            return _truncate_inline(f"{key}={value}")
    return _truncate_inline(", ".join(sorted(str(key) for key in arguments)[:3]))


def _display_tool_name(tool_name: str) -> str:
    """把内部工具名转换成 CLI 可读展示名。"""
    return TOOL_DISPLAY_NAMES.get(tool_name, tool_name)


def _truncate_inline(text: str, limit: int = 88) -> str:
    """把终端单行摘要限制在可读宽度内。"""
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return f"{single_line[: limit - 3]}..."


def _build_stream_output(settings: AgentSettings) -> StreamOutput:
    """根据配置选择纯文本流式输出或 Markdown block 渲染。"""
    if not settings.model.stream:
        return StreamOutput()
    if settings.model.render_markdown and settings.model.markdown_streaming:
        renderer = MarkdownStreamRenderer(console)
        return StreamOutput(handler=renderer.write, renderer=renderer)
    return StreamOutput(handler=_stream_printer)


def _build_model_client(settings: AgentSettings, debug_log: DebugLog | None = None) -> Any:
    """构建主模型客户端；配置多个 profile 时包一层 fallback。"""
    debug_log = debug_log or DebugLog.null()
    profiles = settings.resolved_model_profiles()
    if len(profiles) > 1:
        return FallbackModelClient(profiles, debug_log=debug_log)
    profile = profiles[0]
    return OpenAIModelClient(
        model=profile.model,
        api_key=profile.resolve_api_key(),
        extra_kwargs=profile.client_kwargs(),
        debug_log=debug_log,
    )


def _build_summary_model_client(
    settings: AgentSettings,
    *,
    primary_model_client: Any,
    debug_log: DebugLog | None = None,
) -> Any:
    """构建上下文压缩用模型；未配置 compact_model 时复用主模型。"""
    if not settings.context.compact_model:
        return primary_model_client
    summary_settings = settings.model_copy(
        update={
            "model": settings.model.model_copy(
                update={
                    "name": settings.context.compact_model,
                    "fallback_models": "",
                }
            )
        }
    )
    return _build_model_client(summary_settings, debug_log)


def _effective_settings(settings: AgentSettings, session_id: str | None) -> AgentSettings:
    """应用运行时 session 覆盖，同时不修改调用方传入的 settings。"""
    if session_id is None:
        return settings
    return settings.model_copy(
        update={
            "state": settings.state.model_copy(update={"session_id": session_id})
        }
    )


def _build_debug_log(settings: AgentSettings, project_root: Path) -> DebugLog:
    if not settings.debug.log_enabled:
        return DebugLog.null()
    return DebugLog(
        _resolve_project_path(project_root, settings.debug.log_path),
        session_id=settings.state.session_id,
    )


def _build_trace_logger(settings: AgentSettings, project_root: Path) -> TraceLogger:
    if not settings.trace.enabled:
        return TraceLogger.null()
    return TraceLogger(
        session_id=settings.state.session_id,
        trace_dir=_resolve_project_path(project_root, settings.trace.dir),
        enabled=True,
        sanitize=settings.trace.sanitize,
        html_enabled=settings.trace.html,
        html_include_raw_response=settings.trace.html_include_raw_response,
    )


def _build_state_bundle(
    settings: AgentSettings,
    project_root: Path,
    data_dir: Path,
) -> StateBundle:
    memory_store = (
        MemoryStore(_resolve_project_path(project_root, settings.state.memory_path))
        if settings.state.enable_memory
        else None
    )
    if memory_store is not None:
        memory_store.ensure()

    todo_manager = (
        TodoManager(data_dir / "todos" / f"{safe_session_id(settings.state.session_id)}.json")
        if settings.state.enable_todos
        else None
    )
    skill_root = _resolve_project_path(project_root, settings.state.skills_dir)
    if not skill_root.exists():
        workspace_skills = project_root / "workspace" / "skills"
        if workspace_skills.exists():
            skill_root = workspace_skills
    skill_store = SkillStore(skill_root) if settings.state.enable_skills else None
    task_store = (
        TaskStore(_resolve_project_path(project_root, settings.state.tasks_dir))
        if settings.state.enable_tasks
        else None
    )
    return StateBundle(
        memory_store=memory_store,
        todo_manager=todo_manager,
        skill_store=skill_store,
        task_store=task_store,
        session_store=SessionStore(data_dir / "sessions"),
    )


def _build_team(
    settings: AgentSettings,
    *,
    project_root: Path,
    lane_scheduler: LaneScheduler | None,
    subagent_depth: int,
) -> Team | None:
    if (
        not settings.teams.enabled
        or not settings.teams.members
        or subagent_depth >= settings.teams.max_depth
    ):
        return None

    team = Team(
        log_path=_resolve_project_path(project_root, settings.teams.log_path),
        parent_session_id=settings.state.session_id,
    )
    for member in settings.teams.members:

        async def run_team_member_text(
            task: str,
            child_session_id: str,
            *,
            member_id: str = member.id,
            member_role: str = member.role,
            member_system_prompt: str = member.system_prompt,
        ) -> str:
            """Team 成员实际执行函数：复用主 agent，但关闭 team 递归。"""
            prompt_parts = [
                f"You are team agent {member_id}.",
                member_role,
                member_system_prompt,
                "Return concise findings for the coordinator. Do not address the user directly.",
                "",
                task,
            ]
            child_task = "\n".join(part for part in prompt_parts if part.strip())
            return await _run_agent_text(
                child_task,
                settings=settings.model_copy(
                    update={
                        "model": settings.model.model_copy(update={"stream": False}),
                        "teams": settings.teams.model_copy(update={"enabled": False}),
                    }
                ),
                session_id=child_session_id,
                lane_scheduler=lane_scheduler,
                subagent_depth=subagent_depth + 1,
            )

        team.add_member(
            member.id,
            run_team_member_text,
            role=member.role,
        )
    return team


def _build_prompt_builder(
    *,
    project_root: Path,
    state: StateBundle,
    team: Team | None,
) -> SystemPromptBuilder:
    return SystemPromptBuilder(
        project_root=project_root,
        memory_store=state.memory_store,
        skill_store=state.skill_store,
        task_store=state.task_store,
        todo_manager=state.todo_manager,
        team_members=team.members_summary() if team is not None else None,
    )


def _build_permission_manager(
    settings: AgentSettings,
    project_root: Path,
) -> PermissionManager:
    rules_path: Path | None = None
    if settings.permissions.rules_path:
        rules_path = _resolve_project_path(project_root, settings.permissions.rules_path)
    else:
        default_rules = project_root / ".zx-code" / "permissions.toml"
        if default_rules.exists():
            rules_path = default_rules
    if rules_path:
        return PermissionManager.from_rules_file(
            rules_path,
            tool_policies=settings.permissions.tools,
            default_decision=settings.permissions.default,
        )
    return PermissionManager(
        tool_policies=settings.permissions.tools,
        default_decision=settings.permissions.default,
    )


def _build_subagent_runner(
    settings: AgentSettings,
    *,
    lane_scheduler: LaneScheduler | None,
    subagent_depth: int,
) -> SubagentRunner | None:
    if (
        not settings.subagents.enabled
        or subagent_depth >= settings.subagents.max_depth
    ):
        return None

    async def run_subagent_text(
        task: str,
        child_session_id: str,
        next_depth: int,
    ) -> str:
        """Subagent 实际执行函数：复用主 agent，并递增 depth 防止递归失控。"""
        return await _run_agent_text(
            task,
            settings=settings.model_copy(
                update={"model": settings.model.model_copy(update={"stream": False})}
            ),
            session_id=child_session_id,
            lane_scheduler=lane_scheduler,
            subagent_depth=next_depth,
        )

    return SubagentRunner(
        run_agent_text=run_subagent_text,
        parent_session_id=settings.state.session_id,
        lane_scheduler=lane_scheduler,
        max_depth=settings.subagents.max_depth,
        current_depth=subagent_depth,
    )


def _build_worktree_manager(
    settings: AgentSettings,
    project_root: Path,
) -> WorktreeManager | None:
    if not settings.worktree.isolation_enabled:
        return None
    return WorktreeManager(
        repo_root=project_root,
        worktree_root=_resolve_project_path(project_root, settings.worktree.dir),
    )


def _build_code_context_indexer(
    settings: AgentSettings,
    project_root: Path,
) -> CodeContextIndexer | None:
    if not settings.code_context.enabled:
        return None
    return CodeContextIndexer(
        store=ChromaCodeStore(
            path=_resolve_project_path(project_root, settings.code_context.path),
            collection_name=settings.code_context.collection,
            embedding_function=OpenAICodeEmbeddingFunction(
                model=settings.code_context.embedding_model,
                api_key_env=settings.code_context.embedding_api_key_env,
                base_url=settings.code_context.embedding_base_url,
            ),
        ),
        snapshot_dir=_resolve_project_path(project_root, settings.code_context.snapshot_dir),
        max_result_chars=settings.code_context.max_result_chars,
        max_total_chars=settings.code_context.max_total_chars,
    )


def _build_plugin_manager(
    settings: AgentSettings,
    project_root: Path,
) -> PluginManager:
    plugin_dirs = [
        _resolve_project_path(project_root, plugin_dir)
        for plugin_dir in settings.extensions.plugin_dirs
    ]
    default_plugin_dir = project_root / ".zx-code" / "plugins"
    if default_plugin_dir.exists():
        plugin_dirs.append(default_plugin_dir)
    return PluginManager(plugin_dirs)


def _build_hook_runner(settings: AgentSettings, project_root: Path) -> HookRunner:
    hooks_path: Path | None = None
    if settings.extensions.hooks_path:
        hooks_path = _resolve_project_path(project_root, settings.extensions.hooks_path)
    else:
        default_hooks = project_root / ".zx-code" / "hooks.toml"
        if default_hooks.exists():
            hooks_path = default_hooks
    return HookRunner.from_file(hooks_path) if hooks_path else HookRunner.empty()


def _build_context_guard(
    settings: AgentSettings,
    summary_client: ModelSummaryClient,
) -> ContextGuard:
    return ContextGuard(
        max_tokens=settings.context.max_tokens,
        keep_recent=settings.context.keep_recent,
        compact_model=settings.context.compact_model,
        model=settings.model.name,
        summary_client=summary_client,
    )


def _build_runtime(
    settings: AgentSettings,
    *,
    session_id: str | None = None,
    lane_scheduler: LaneScheduler | None = None,
    subagent_depth: int = 0,
) -> AgentRuntime:
    """按依赖顺序构建一次 agent turn 需要的全部运行时对象。

    返回 typed runtime 是为了让 CLI/subagent 入口共享同一组组件，而不是在多个调用点
    复制装配逻辑。这里是组合根，业务模块不应反向导入 runner/builder。
    """
    effective_settings = _effective_settings(settings, session_id)
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, effective_settings.state.data_dir)
    debug_log = _build_debug_log(effective_settings, project_root)
    trace_logger = _build_trace_logger(effective_settings, project_root)
    state = _build_state_bundle(effective_settings, project_root, data_dir)
    team = _build_team(
        effective_settings,
        project_root=project_root,
        lane_scheduler=lane_scheduler,
        subagent_depth=subagent_depth,
    )
    prompt_builder = _build_prompt_builder(
        project_root=project_root,
        state=state,
        team=team,
    )
    permission_manager = _build_permission_manager(effective_settings, project_root)
    subagent_runner = _build_subagent_runner(
        effective_settings,
        lane_scheduler=lane_scheduler,
        subagent_depth=subagent_depth,
    )
    worktree_manager = _build_worktree_manager(effective_settings, project_root)
    code_context_indexer = _build_code_context_indexer(effective_settings, project_root)
    tool_deps = ToolRuntimeDeps(
        permission_manager=permission_manager,
        approval_callback=_approval_prompt,
        todo_manager=state.todo_manager,
        memory_store=state.memory_store,
        skill_store=state.skill_store,
        task_store=state.task_store,
        subagent_runner=subagent_runner,
        team=team,
        team_timeout_s=effective_settings.teams.timeout_s,
        worktree_manager=worktree_manager,
        code_context_indexer=code_context_indexer,
        debug_log=debug_log,
    )
    registry = build_registry_from_deps(tool_deps)
    plugin_manager = _build_plugin_manager(effective_settings, project_root)
    for plugin_tool in plugin_manager.load_tools():
        registry.register(plugin_tool)
    hook_runner = _build_hook_runner(effective_settings, project_root)

    model_client = _build_model_client(effective_settings, debug_log)
    summary_model_client = _build_summary_model_client(
        effective_settings,
        primary_model_client=model_client,
        debug_log=debug_log,
    )
    summary_client = ModelSummaryClient(summary_model_client)
    context_guard = _build_context_guard(effective_settings, summary_client)
    runtime = AgentRuntime(
        settings=effective_settings,
        project_root=project_root,
        data_dir=data_dir,
        config=effective_settings.to_runtime_config(),
        tool_registry=registry,
        state=state,
        services=RuntimeServices(
            debug_log=debug_log,
            trace_logger=trace_logger,
            permission_manager=permission_manager,
            hook_runner=hook_runner,
            context_guard=context_guard,
            model_client=model_client,
            summary_model_client=summary_client,
            plugin_manager=plugin_manager,
            prompt_builder=prompt_builder,
            stream_output=_build_stream_output(effective_settings),
            lane_scheduler=lane_scheduler,
        ),
        extensions=RuntimeExtensions(
            subagent_runner=subagent_runner,
            team=team,
            worktree_manager=worktree_manager,
            code_context_indexer=code_context_indexer,
        ),
    )
    debug_log.event(
        "runtime.built",
        {
            "model": effective_settings.model.name,
            "session_id": effective_settings.state.session_id,
            "data_dir": str(data_dir),
            "team_members": team.members_summary() if team is not None else [],
        },
        level="info",
    )
    _refresh_system_prompt(runtime)
    return runtime


def _refresh_system_prompt(runtime: AgentRuntime) -> None:
    """根据当前工具 schema 和状态存储重新生成 system prompt。"""
    runtime.config = runtime.config.model_copy(
        update={
            "system_prompt": runtime.services.prompt_builder.build(
                runtime.config,
                tool_schemas=runtime.tool_registry.schemas(),
            )
        }
    )


async def _attach_mcp_tools(
    runtime: AgentRuntime,
    settings: AgentSettings,
) -> MCPToolRouter | None:
    """连接 MCP 服务器，把发现到的外部工具注册进当前 runtime。"""
    if not settings.extensions.mcp_servers:
        return None

    router = MCPToolRouter(
        {
            server.name: StdioMCPClient(
                name=server.name,
                command=server.command,
                args=server.args,
                env=server.env,
            )
            for server in settings.extensions.mcp_servers
        },
        permission_manager=runtime.services.permission_manager,
    )
    registry = runtime.tool_registry
    try:
        for tool in await router.discover_tools():
            registry.register(tool)
        _refresh_system_prompt(runtime)
        runtime.extensions.mcp_router = router
    except Exception:
        await router.close()
        raise
    return router


async def _run_agent_text(
    task: str,
    *,
    settings: AgentSettings,
    session_id: str | None = None,
    lane_scheduler: LaneScheduler | None = None,
    subagent_depth: int = 0,
) -> str:
    """运行一次文本任务并返回最终回答。

    这是 CLI、subagent/team 复用的内部入口。它负责创建 runtime、
    挂载 MCP、调用 run_task，并在 finally 中关闭 team/MCP/stream 状态。
    """
    runtime = _build_runtime(
        settings,
        session_id=session_id,
        lane_scheduler=lane_scheduler,
        subagent_depth=subagent_depth,
    )
    mcp_router = await _attach_mcp_tools(runtime, runtime.settings)
    stream_output = runtime.services.stream_output
    progress_reporter = (
        CLIProgressReporter(console, flush_output=stream_output.flush)
        if settings.model.stream
        else None
    )

    try:
        result = await run_task(
            task,
            model_client=runtime.services.model_client,
            tool_registry=runtime.tool_registry,
            config=runtime.config,
            stream_handler=stream_output.handler,
            session_store=runtime.session_store,
            context_guard=runtime.services.context_guard,
            prompt_builder=runtime.services.prompt_builder,
            hook_runner=runtime.services.hook_runner,
            debug_log=runtime.services.debug_log,
            trace_logger=runtime.services.trace_logger,
            project_root=runtime.project_root,
            progress_handler=(
                progress_reporter.handle
                if progress_reporter is not None
                else None
            ),
        )
    except AgentError as exc:
        raise exc
    finally:
        if progress_reporter is not None:
            progress_reporter.stop()
        stream_output.flush()
        team = runtime.extensions.team
        if team is not None:
            await team.shutdown()
        if mcp_router is not None:
            await mcp_router.close()

    if settings.model.stream and result.final_text and stream_output.renderer is None:
        console.print()
    return result.final_text
