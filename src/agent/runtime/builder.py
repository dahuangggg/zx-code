"""runtime.builder — Agent 运行时的组合根（Composition Root）。

``_build_runtime()`` 是整个框架的"装配工厂"，按依赖顺序实例化所有组件：

  MemoryStore → TodoManager → SkillStore → TaskStore
  → SystemPromptBuilder → PermissionManager → SubagentRunner
  → WorktreeManager → ToolRegistry（注册所有工具）
  → PluginManager（动态加载插件工具）
  → HookRunner → ContextGuard → ModelClient
  → SessionStore

返回一个 dict（runtime），包含 loop.py 所需的所有依赖，
各组件通过此 dict 传递，避免全局状态。

``_attach_mcp_tools()`` 在运行时异步连接 MCP 服务器，
发现工具后注册到 ToolRegistry 并刷新 system prompt。
"""

from __future__ import annotations


from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.config import AgentSettings
from agent.core.context import ContextGuard
from agent.debuglog import DebugLog
from agent.errors import AgentError
from agent.hooks import HookRunner
from agent.scheduling.lanes import LaneScheduler
from agent.core.loop import run_task
from agent.mcp import MCPToolRouter, StdioMCPClient
from agent.state.memory import MemoryStore
from agent.permissions import PermissionManager
from agent.plugins import PluginManager
from agent.profiles import FallbackModelClient
from agent.prompt import SystemPromptBuilder
from agent.providers.litellm_client import LiteLLMModelClient
from agent.runtime.markdown_stream import MarkdownStreamRenderer
from agent.runtime.utils import _approval_prompt, _resolve_project_path, _stream_printer, console
from agent.state.sessions import SessionStore, safe_session_id
from agent.state.skills import SkillStore
from agent.agents.subagent import SubagentRunner
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager
from agent.tools import build_default_registry
from agent.agents.worktree import WorktreeManager
from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.indexer import CodeContextIndexer


@dataclass
class StreamOutput:
    handler: Callable[[str], Any] | None = None
    renderer: MarkdownStreamRenderer | None = None

    def flush(self) -> None:
        if self.renderer is not None:
            self.renderer.flush()


def _build_stream_output(settings: AgentSettings) -> StreamOutput:
    if not settings.stream:
        return StreamOutput()
    if settings.render_markdown and settings.markdown_streaming:
        renderer = MarkdownStreamRenderer(console)
        return StreamOutput(handler=renderer.write, renderer=renderer)
    return StreamOutput(handler=_stream_printer)


def _build_model_client(settings: AgentSettings, debug_log: DebugLog | None = None) -> Any:
    profiles = settings.resolved_model_profiles()
    if len(profiles) > 1:
        return FallbackModelClient(profiles, debug_log=debug_log)
    profile = profiles[0]
    return LiteLLMModelClient(
        model=profile.model,
        extra_kwargs=profile.litellm_kwargs(),
        debug_log=debug_log,
    )


def _build_runtime(
    settings: AgentSettings,
    *,
    session_id: str | None = None,
    lane_scheduler: LaneScheduler | None = None,
    subagent_depth: int = 0,
) -> dict[str, Any]:
    effective_settings = (
        settings.model_copy(update={"session_id": session_id})
        if session_id is not None
        else settings
    )
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, effective_settings.data_dir)
    debug_log = (
        DebugLog(
            _resolve_project_path(project_root, effective_settings.debug_log_path),
            session_id=effective_settings.session_id,
        )
        if effective_settings.debug_log_enabled
        else None
    )
    memory_store = (
        MemoryStore(_resolve_project_path(project_root, effective_settings.memory_path))
        if effective_settings.enable_memory
        else None
    )
    if memory_store is not None:
        memory_store.ensure()

    todo_manager = (
        TodoManager(data_dir / "todos" / f"{safe_session_id(effective_settings.session_id)}.json")
        if effective_settings.enable_todos
        else None
    )
    skill_root = _resolve_project_path(project_root, effective_settings.skills_dir)
    if not skill_root.exists():
        workspace_skills = project_root / "workspace" / "skills"
        if workspace_skills.exists():
            skill_root = workspace_skills
    skill_store = (
        SkillStore(skill_root)
        if effective_settings.enable_skills
        else None
    )
    task_store = (
        TaskStore(_resolve_project_path(project_root, effective_settings.tasks_dir))
        if effective_settings.enable_tasks
        else None
    )
    prompt_builder = SystemPromptBuilder(
        project_root=project_root,
        memory_store=memory_store,
        skill_store=skill_store,
        task_store=task_store,
        todo_manager=todo_manager,
    )
    # Resolve rules file: explicit setting > project default
    rules_path: Path | None = None
    if effective_settings.permission_rules_path:
        rules_path = _resolve_project_path(project_root, effective_settings.permission_rules_path)
    else:
        default_rules = project_root / ".zx-code" / "permissions.toml"
        if default_rules.exists():
            rules_path = default_rules
    permission_manager = PermissionManager.from_rules_file(
        rules_path or "",
        tool_policies=effective_settings.permission_tools,
        default_decision=effective_settings.permission_default,
    ) if rules_path else PermissionManager(
        tool_policies=effective_settings.permission_tools,
        default_decision=effective_settings.permission_default,
    )
    subagent_runner: SubagentRunner | None = None
    if (
        effective_settings.enable_subagents
        and subagent_depth < effective_settings.subagent_max_depth
    ):

        async def run_subagent_text(
            task: str,
            child_session_id: str,
            next_depth: int,
        ) -> str:
            return await _run_agent_text(
                task,
                settings=effective_settings.model_copy(update={"stream": False}),
                session_id=child_session_id,
                lane_scheduler=lane_scheduler,
                subagent_depth=next_depth,
            )

        subagent_runner = SubagentRunner(
            run_agent_text=run_subagent_text,
            parent_session_id=effective_settings.session_id,
            lane_scheduler=lane_scheduler,
            max_depth=effective_settings.subagent_max_depth,
            current_depth=subagent_depth,
        )

    worktree_manager = (
        WorktreeManager(
            repo_root=project_root,
            worktree_root=_resolve_project_path(project_root, effective_settings.worktree_dir),
        )
        if effective_settings.enable_worktree_isolation
        else None
    )
    code_context_indexer = (
        CodeContextIndexer(
            store=ChromaCodeStore(
                path=_resolve_project_path(project_root, effective_settings.code_context_path),
                collection_name=effective_settings.code_context_collection,
            ),
            snapshot_dir=_resolve_project_path(project_root, effective_settings.code_context_snapshot_dir),
            max_result_chars=effective_settings.code_context_max_result_chars,
            max_total_chars=effective_settings.code_context_max_total_chars,
        )
        if effective_settings.code_context_enabled
        else None
    )
    registry = build_default_registry(
        permission_manager=permission_manager,
        approval_callback=_approval_prompt,
        todo_manager=todo_manager,
        memory_store=memory_store,
        skill_store=skill_store,
        task_store=task_store,
        subagent_runner=subagent_runner,
        worktree_manager=worktree_manager,
        code_context_indexer=code_context_indexer,
        debug_log=debug_log,
    )
    plugin_dirs = [
        _resolve_project_path(project_root, plugin_dir)
        for plugin_dir in effective_settings.plugin_dirs
    ]
    default_plugin_dir = project_root / ".zx-code" / "plugins"
    if default_plugin_dir.exists():
        plugin_dirs.append(default_plugin_dir)
    for plugin_tool in PluginManager(plugin_dirs).load_tools():
        registry.register(plugin_tool)
    # Resolve hooks file: explicit setting > project default
    hooks_path: Path | None = None
    if effective_settings.hooks_path:
        hooks_path = _resolve_project_path(project_root, effective_settings.hooks_path)
    else:
        default_hooks = project_root / ".zx-code" / "hooks.toml"
        if default_hooks.exists():
            hooks_path = default_hooks
    hook_runner = HookRunner.from_file(hooks_path) if hooks_path else HookRunner.empty()

    runtime = {
        "config": effective_settings.to_runtime_config(),
        "hook_runner": hook_runner,
        "permission_manager": permission_manager,
        "context_guard": ContextGuard(
            max_tokens=effective_settings.context_max_tokens,
            keep_recent=effective_settings.context_keep_recent,
            tool_result_max_chars=effective_settings.context_tool_result_max_chars,
            compact_model=effective_settings.compact_model,
            model=effective_settings.model,
        ),
        "model_client": _build_model_client(effective_settings, debug_log),
        "settings": effective_settings,
        "prompt_builder": prompt_builder,
        "session_store": SessionStore(data_dir / "sessions"),
        "tool_registry": registry,
        "debug_log": debug_log,
    }
    if debug_log is not None:
        debug_log.event(
            "runtime.built",
            {
                "model": effective_settings.model,
                "session_id": effective_settings.session_id,
                "data_dir": str(data_dir),
                "debug_log_path": str(debug_log.path),
            },
        )
    _refresh_system_prompt(runtime)
    return runtime


def _refresh_system_prompt(runtime: dict[str, Any]) -> None:
    config = runtime["config"]
    runtime["config"] = config.model_copy(
        update={
            "system_prompt": runtime["prompt_builder"].build(
                config,
                tool_schemas=runtime["tool_registry"].schemas(),
            )
        }
    )


async def _attach_mcp_tools(
    runtime: dict[str, Any],
    settings: AgentSettings,
) -> MCPToolRouter | None:
    if not settings.mcp_servers:
        return None

    router = MCPToolRouter(
        {
            server.name: StdioMCPClient(
                name=server.name,
                command=server.command,
                args=server.args,
                env=server.env,
            )
            for server in settings.mcp_servers
        },
        permission_manager=runtime.get("permission_manager"),
    )
    registry = runtime["tool_registry"]
    try:
        for tool in await router.discover_tools():
            registry.register(tool)
        _refresh_system_prompt(runtime)
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
    runtime = _build_runtime(
        settings,
        session_id=session_id,
        lane_scheduler=lane_scheduler,
        subagent_depth=subagent_depth,
    )
    mcp_router = await _attach_mcp_tools(runtime, runtime["settings"])
    stream_output = _build_stream_output(settings)

    try:
        result = await run_task(
            task,
            model_client=runtime["model_client"],
            tool_registry=runtime["tool_registry"],
            config=runtime["config"],
            stream_handler=stream_output.handler,
            session_store=runtime["session_store"],
            context_guard=runtime["context_guard"],
            prompt_builder=runtime["prompt_builder"],
            hook_runner=runtime["hook_runner"],
            debug_log=runtime.get("debug_log"),
        )
    except AgentError as exc:
        raise exc
    finally:
        stream_output.flush()
        if mcp_router is not None:
            await mcp_router.close()

    if settings.stream and result.final_text and stream_output.renderer is None:
        console.print()
    return result.final_text
