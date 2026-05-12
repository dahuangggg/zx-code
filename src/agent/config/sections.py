from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.mcp import MCPServerConfig
from agent.models import DMScope, RuntimeConfig
from agent.permissions import PermissionDecision
from agent.profiles import ModelProfile


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "openai/gpt-5.4-mini"
    fallback_models: str = "openai/gpt-5.5"
    reasoning_effort: str = ""
    profiles: list[ModelProfile] = Field(default_factory=list)
    max_iterations: int = 30
    timeout_s: float = 300.0
    stream: bool = True
    render_markdown: bool = True
    markdown_streaming: bool = True


class ContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = 128000
    keep_recent: int = 15
    tool_result_max_chars: int = 6000
    compact_model: str = ""


class StateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = "default"
    data_dir: str = ".agent"
    memory_path: str = ".memory/MEMORY.md"
    enable_memory: bool = True
    enable_skills: bool = True
    skills_dir: str = "skills"
    enable_todos: bool = True
    enable_tasks: bool = True
    tasks_dir: str = ".tasks"


class PermissionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: PermissionDecision = "allow"
    tools: dict[str, PermissionDecision] = Field(default_factory=dict)
    rules_path: str = ""


class TelegramSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = ""
    offset: int | None = None
    timeout_s: int = 30
    allowed_chats: str = ""
    text_coalesce_s: float = 1.0
    media_group_coalesce_s: float = 0.5


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "cli"
    account_id: str = "local"
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


class RoutingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = "default"
    default_agent_id: str = "default"
    force_agent_id: str = ""
    dm_scope: DMScope = "per-account-channel-peer"


class DeliverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 5
    base_delay_s: float = 1.0
    max_delay_s: float = 300.0
    jitter_s: float = 1.0
    daemon_interval_s: float = 1.0


class SchedulingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeat_enabled: bool = False
    heartbeat_interval_s: float = 300.0
    heartbeat_min_idle_s: float = 30.0
    heartbeat_channel: str = ""
    heartbeat_to: str = ""
    heartbeat_prompt: str = "Heartbeat check. Reply HEARTBEAT_OK if no user-facing update is needed."
    heartbeat_sentinel: str = "HEARTBEAT_OK"
    cron_jobs_path: str = ""


class SubagentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_depth: int = Field(default=1, ge=0)


class WorktreeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isolation_enabled: bool = False
    dir: str = ".agent/worktrees"


class CodeContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    path: str = ".agent/code-context/chroma"
    snapshot_dir: str = ".agent/code-context/snapshots"
    collection: str = "agent_code_context"
    top_k: int = Field(default=5, ge=1, le=20)
    max_result_chars: int = Field(default=4000, ge=500)
    max_total_chars: int = Field(default=12000, ge=1000)


class DebugSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_enabled: bool = False
    log_path: str = ".agent/debug.jsonl"


class ExtensionSettings(BaseModel):
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
    "channel": ("channel", "name"),
    "account_id": ("channel", "account_id"),
    "telegram_token": ("channel", "telegram", "token"),
    "telegram_offset": ("channel", "telegram", "offset"),
    "telegram_timeout_s": ("channel", "telegram", "timeout_s"),
    "telegram_allowed_chats": ("channel", "telegram", "allowed_chats"),
    "telegram_text_coalesce_s": ("channel", "telegram", "text_coalesce_s"),
    "telegram_media_group_coalesce_s": ("channel", "telegram", "media_group_coalesce_s"),
    "agent_id": ("routing", "agent_id"),
    "default_agent_id": ("routing", "default_agent_id"),
    "force_agent_id": ("routing", "force_agent_id"),
    "dm_scope": ("routing", "dm_scope"),
    "delivery_max_attempts": ("delivery", "max_attempts"),
    "delivery_base_delay_s": ("delivery", "base_delay_s"),
    "delivery_max_delay_s": ("delivery", "max_delay_s"),
    "delivery_jitter_s": ("delivery", "jitter_s"),
    "delivery_daemon_interval_s": ("delivery", "daemon_interval_s"),
    "heartbeat_enabled": ("scheduling", "heartbeat_enabled"),
    "heartbeat_interval_s": ("scheduling", "heartbeat_interval_s"),
    "heartbeat_min_idle_s": ("scheduling", "heartbeat_min_idle_s"),
    "heartbeat_channel": ("scheduling", "heartbeat_channel"),
    "heartbeat_to": ("scheduling", "heartbeat_to"),
    "heartbeat_prompt": ("scheduling", "heartbeat_prompt"),
    "heartbeat_sentinel": ("scheduling", "heartbeat_sentinel"),
    "cron_jobs_path": ("scheduling", "cron_jobs_path"),
    "enable_subagents": ("subagents", "enabled"),
    "subagent_max_depth": ("subagents", "max_depth"),
    "enable_worktree_isolation": ("worktree", "isolation_enabled"),
    "worktree_dir": ("worktree", "dir"),
    "code_context_enabled": ("code_context", "enabled"),
    "code_context_path": ("code_context", "path"),
    "code_context_snapshot_dir": ("code_context", "snapshot_dir"),
    "code_context_collection": ("code_context", "collection"),
    "code_context_top_k": ("code_context", "top_k"),
    "code_context_max_result_chars": ("code_context", "max_result_chars"),
    "code_context_max_total_chars": ("code_context", "max_total_chars"),
    "debug_log_enabled": ("debug", "log_enabled"),
    "debug_log_path": ("debug", "log_path"),
    "hooks_path": ("extensions", "hooks_path"),
    "mcp_servers": ("extensions", "mcp_servers"),
    "plugin_dirs": ("extensions", "plugin_dirs"),
}


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelSettings = Field(default_factory=ModelSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    state: StateSettings = Field(default_factory=StateSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    channel: ChannelSettings = Field(default_factory=ChannelSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    delivery: DeliverySettings = Field(default_factory=DeliverySettings)
    scheduling: SchedulingSettings = Field(default_factory=SchedulingSettings)
    subagents: SubagentSettings = Field(default_factory=SubagentSettings)
    worktree: WorktreeSettings = Field(default_factory=WorktreeSettings)
    code_context: CodeContextSettings = Field(default_factory=CodeContextSettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)
    extensions: ExtensionSettings = Field(default_factory=ExtensionSettings)

    @model_validator(mode="before")
    @classmethod
    def _fold_flat_settings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        for old_key, path in _FLAT_ALIASES.items():
            if old_key in raw:
                if old_key in {"model", "channel"} and isinstance(raw[old_key], dict):
                    continue
                _set_nested(raw, path, raw.pop(old_key))
        if "telegram" in raw:
            _set_nested(raw, ("channel", "telegram"), raw.pop("telegram"))
        return raw

    def to_runtime_config(self, *, system_prompt: str = "") -> RuntimeConfig:
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
        )

    def resolved_model_profiles(self) -> list[ModelProfile]:
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
