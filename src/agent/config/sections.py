"""Agent 配置 section 定义。

本模块只描述 CLI-only runtime 仍然使用的配置：model、context、state、
permissions、subagents、teams、worktree、CodeContext、debug、trace 和 extensions。
AgentSettings 还兼容历史扁平字段，通过 _FLAT_ALIASES 折叠到新的嵌套结构。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.mcp import MCPServerConfig
from agent.models import RuntimeConfig
from agent.permissions import PermissionDecision
from agent.profiles import ModelProfile


class ModelSettings(BaseModel):
    """模型选择、fallback、推理强度和流式输出配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str = "gpt-5.4"
    fallback_models: str = "gpt-5.4"
    reasoning_effort: str = ""
    profiles: list[ModelProfile] = Field(default_factory=list)
    max_iterations: int = 30
    timeout_s: float = 300.0
    stream: bool = True
    render_markdown: bool = True
    markdown_streaming: bool = True


class ContextSettings(BaseModel):
    """上下文预算、近期消息保留和工具结果截断配置。"""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int = 128000
    keep_recent: int = 15
    tool_result_max_chars: int = 6000
    compact_model: str = ""


class StateSettings(BaseModel):
    """本地状态目录和 memory/skill/todo/task 持久化开关。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = "default"
    data_dir: str = ".zx-code/state"
    memory_path: str = ".zx-code/state/memory/MEMORY.md"
    enable_memory: bool = True
    enable_skills: bool = True
    skills_dir: str = "skills"
    enable_todos: bool = True
    enable_tasks: bool = True
    tasks_dir: str = ".zx-code/state/tasks"


class PermissionSettings(BaseModel):
    """工具权限默认策略、按工具覆盖和外部规则文件配置。"""

    model_config = ConfigDict(extra="forbid")

    default: PermissionDecision = "allow"
    tools: dict[str, PermissionDecision] = Field(default_factory=dict)
    rules_path: str = ""


class SubagentSettings(BaseModel):
    """子代理开关和最大嵌套深度。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_depth: int = Field(default=1, ge=0)


class TeamMemberSettings(BaseModel):
    """团队成员声明，用于构建可路由的 agent team。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    role: str = ""
    system_prompt: str = ""


class TeamSettings(BaseModel):
    """agent team 的启用状态、消息日志和成员列表配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    log_path: str = ".zx-code/logs/team/messages.jsonl"
    timeout_s: float = Field(default=30.0, gt=0)
    max_depth: int = Field(default=1, ge=0)
    members: list[TeamMemberSettings] = Field(default_factory=list)


class WorktreeSettings(BaseModel):
    """子代理 worktree 隔离开关和 worktree 根目录。"""

    model_config = ConfigDict(extra="forbid")

    isolation_enabled: bool = False
    dir: str = ".zx-code/state/worktrees"


class CodeContextSettings(BaseModel):
    """CodeContext 索引、快照和检索结果预算配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    path: str = ".zx-code/cache/code-context/chroma"
    snapshot_dir: str = ".zx-code/cache/code-context/snapshots"
    collection: str = "agent_code_context"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key_env: str = "OPENAI_API_KEY"
    embedding_base_url: str = ""
    top_k: int = Field(default=5, ge=1, le=20)
    max_result_chars: int = Field(default=4000, ge=500)
    max_total_chars: int = Field(default=12000, ge=1000)


class DebugSettings(BaseModel):
    """可选 debug JSONL 日志配置。"""

    model_config = ConfigDict(extra="forbid")

    log_enabled: bool = False
    log_path: str = ".zx-code/logs/debug.jsonl"


class TraceSettings(BaseModel):
    """审计 trace、脱敏和 HTML 报告配置。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dir: str = ".zx-code/logs/traces"
    sanitize: bool = True
    html: bool = False
    html_include_raw_response: bool = False


class ExtensionSettings(BaseModel):
    """hooks、MCP servers 和 plugin directories 扩展配置。"""

    model_config = ConfigDict(extra="forbid")

    hooks_path: str = ""
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    plugin_dirs: list[str] = Field(default_factory=list)


_FLAT_ALIASES: dict[str, tuple[str, ...]] = {
    "model": ("model", "name"),
    "fallback_models": ("model", "fallback_models"),
    "reasoning_effort": ("model", "reasoning_effort"),
    "model_profiles": ("model", "profiles"),
    "max_iterations": ("model", "max_iterations"),
    "model_timeout_s": ("model", "timeout_s"),
    "stream": ("model", "stream"),
    "render_markdown": ("model", "render_markdown"),
    "markdown_streaming": ("model", "markdown_streaming"),
    "context_max_tokens": ("context", "max_tokens"),
    "context_keep_recent": ("context", "keep_recent"),
    "context_tool_result_max_chars": ("context", "tool_result_max_chars"),
    "compact_model": ("context", "compact_model"),
    "session_id": ("state", "session_id"),
    "data_dir": ("state", "data_dir"),
    "memory_path": ("state", "memory_path"),
    "enable_memory": ("state", "enable_memory"),
    "enable_skills": ("state", "enable_skills"),
    "skills_dir": ("state", "skills_dir"),
    "enable_todos": ("state", "enable_todos"),
    "enable_tasks": ("state", "enable_tasks"),
    "tasks_dir": ("state", "tasks_dir"),
    "permission_default": ("permissions", "default"),
    "permission_tools": ("permissions", "tools"),
    "permission_rules_path": ("permissions", "rules_path"),
    "enable_subagents": ("subagents", "enabled"),
    "subagent_max_depth": ("subagents", "max_depth"),
    "enable_teams": ("teams", "enabled"),
    "team_log_path": ("teams", "log_path"),
    "team_timeout_s": ("teams", "timeout_s"),
    "team_max_depth": ("teams", "max_depth"),
    "team_members": ("teams", "members"),
    "enable_worktree_isolation": ("worktree", "isolation_enabled"),
    "worktree_dir": ("worktree", "dir"),
    "code_context_enabled": ("code_context", "enabled"),
    "code_context_path": ("code_context", "path"),
    "code_context_snapshot_dir": ("code_context", "snapshot_dir"),
    "code_context_collection": ("code_context", "collection"),
    "code_context_embedding_model": ("code_context", "embedding_model"),
    "code_context_embedding_api_key_env": ("code_context", "embedding_api_key_env"),
    "code_context_embedding_base_url": ("code_context", "embedding_base_url"),
    "code_context_top_k": ("code_context", "top_k"),
    "code_context_max_result_chars": ("code_context", "max_result_chars"),
    "code_context_max_total_chars": ("code_context", "max_total_chars"),
    "debug_log_enabled": ("debug", "log_enabled"),
    "debug_log_path": ("debug", "log_path"),
    "trace_enabled": ("trace", "enabled"),
    "trace_dir": ("trace", "dir"),
    "trace_sanitize": ("trace", "sanitize"),
    "trace_html": ("trace", "html"),
    "trace_html_include_raw_response": ("trace", "html_include_raw_response"),
    "hooks_path": ("extensions", "hooks_path"),
    "mcp_servers": ("extensions", "mcp_servers"),
    "plugin_dirs": ("extensions", "plugin_dirs"),
}


class AgentSettings(BaseModel):
    """完整 agent 配置。"""

    model_config = ConfigDict(extra="forbid")

    model: ModelSettings = Field(default_factory=ModelSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    state: StateSettings = Field(default_factory=StateSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    subagents: SubagentSettings = Field(default_factory=SubagentSettings)
    teams: TeamSettings = Field(default_factory=TeamSettings)
    worktree: WorktreeSettings = Field(default_factory=WorktreeSettings)
    code_context: CodeContextSettings = Field(default_factory=CodeContextSettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    extensions: ExtensionSettings = Field(default_factory=ExtensionSettings)

    @model_validator(mode="before")
    @classmethod
    def _fold_flat_settings(cls, data: Any) -> Any:
        """把兼容的扁平配置字段折叠到当前嵌套 section。"""
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        for old_key, path in _FLAT_ALIASES.items():
            if old_key in raw:
                if old_key == "model" and isinstance(raw[old_key], dict):
                    continue
                _set_nested(raw, path, raw.pop(old_key))
        return raw

    def to_runtime_config(self, *, system_prompt: str = "") -> RuntimeConfig:
        """提取主循环需要的 RuntimeConfig。"""
        return RuntimeConfig(
            model=self.model.name,
            system_prompt=system_prompt,
            max_iterations=self.model.max_iterations,
            model_timeout_s=self.model.timeout_s,
            stream=self.model.stream,
            session_id=self.state.session_id,
            data_dir=self.state.data_dir,
            context_max_tokens=self.context.max_tokens,
            context_keep_recent=self.context.keep_recent,
            context_tool_result_max_chars=self.context.tool_result_max_chars,
            memory_path=self.state.memory_path,
            enable_memory=self.state.enable_memory,
            enable_todos=self.state.enable_todos,
            debug_log_enabled=self.debug.log_enabled,
            debug_log_path=self.debug.log_path,
            trace_enabled=self.trace.enabled,
            trace_dir=self.trace.dir,
            trace_sanitize=self.trace.sanitize,
            trace_html=self.trace.html,
            trace_html_include_raw_response=self.trace.html_include_raw_response,
        )

    def resolved_model_profiles(self) -> list[ModelProfile]:
        """展开主模型和 fallback 模型 profile。"""
        profiles = list(self.model.profiles)
        if not profiles:
            profiles.append(
                ModelProfile(
                    name="primary",
                    model=self.model.name,
                    reasoning_effort=self.model.reasoning_effort,
                )
            )
        for index, model in enumerate(_split_csv(self.model.fallback_models), start=1):
            profiles.append(
                ModelProfile(
                    name=f"fallback-{index}",
                    model=model,
                    reasoning_effort=self.model.reasoning_effort,
                )
            )
        return profiles


def _set_nested(raw: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = raw
    for key in path[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            existing = {}
            cursor[key] = existing
        cursor = existing
    cursor[path[-1]] = value


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
