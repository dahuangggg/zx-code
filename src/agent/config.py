"""config — Agent 配置管理。

提供两个类：

AgentSettings
    完整的配置模型，涵盖所有 CLI 参数和环境变量可设定的选项。
    通过 ``ConfigLoader.load()`` 从 TOML 文件 + CLI 覆盖项构建。
    通过 ``to_runtime_config()`` 转为 ``RuntimeConfig`` 传入核心循环。

ConfigLoader
    三层合并配置加载器，优先级从低到高：
      1. 用户级配置  (~/.zx-code/config.toml)
      2. 项目级配置  (.zx-code/config.toml)
      3. CLI 参数覆盖 (通过 main.py 的 typer 命令)
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.mcp import MCPServerConfig
from agent.models import DMScope, RuntimeConfig
from agent.permissions import PermissionDecision
from agent.profiles import ModelProfile


class AgentSettings(BaseModel):
    """Agent 的完整配置，字段对应所有可配置项。

    分组说明：
      模型相关    — model, fallback_models, model_profiles, max_iterations ...
      上下文管理  — context_max_tokens, context_keep_recent, compact_model ...
      持久化路径  — data_dir, memory_path, skills_dir, tasks_dir ...
      权限系统    — permission_default, permission_tools, permission_rules_path
      外部通道    — channel, telegram_*, feishu_* ...
      调度系统    — heartbeat_*, cron_jobs_path
      子代理      — enable_subagents, subagent_max_depth
      扩展        — mcp_servers, plugin_dirs, hooks_path
    """

    model_config = ConfigDict(extra="forbid")

    model: str = "openai/gpt-4o-mini"
    fallback_models: str = ""
    model_profiles: list[ModelProfile] = Field(default_factory=list)
    max_iterations: int = 8
    model_timeout_s: float = 60.0
    stream: bool = True
    session_id: str = "default"
    data_dir: str = ".agent"
    context_max_tokens: int = 12000
    context_keep_recent: int = 6
    context_tool_result_max_chars: int = 6000
    compact_model: str = ""
    memory_path: str = ".memory/MEMORY.md"
    enable_memory: bool = True
    enable_skills: bool = True
    skills_dir: str = "skills"
    enable_todos: bool = True
    enable_tasks: bool = True
    tasks_dir: str = ".tasks"
    permission_default: PermissionDecision = "allow"
    permission_tools: dict[str, PermissionDecision] = Field(default_factory=dict)
    permission_rules_path: str = ""
    hooks_path: str = ""
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    plugin_dirs: list[str] = Field(default_factory=list)
    agent_id: str = "default"
    default_agent_id: str = "default"
    force_agent_id: str = ""
    dm_scope: DMScope = "per-account-channel-peer"
    channel: str = "cli"
    account_id: str = "local"
    telegram_token: str = ""
    telegram_offset: int | None = None
    telegram_timeout_s: int = 30
    telegram_allowed_chats: str = ""
    telegram_text_coalesce_s: float = 1.0
    telegram_media_group_coalesce_s: float = 0.5
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_bot_open_id: str = ""
    feishu_is_lark: bool = False
    feishu_webhook_host: str = "127.0.0.1"
    feishu_webhook_port: int = 0
    feishu_receive_timeout_s: float = 30.0
    delivery_max_attempts: int = 5
    delivery_base_delay_s: float = 1.0
    delivery_max_delay_s: float = 300.0
    delivery_jitter_s: float = 1.0
    delivery_daemon_interval_s: float = 1.0
    heartbeat_enabled: bool = False
    heartbeat_interval_s: float = 300.0
    heartbeat_min_idle_s: float = 30.0
    heartbeat_channel: str = ""
    heartbeat_to: str = ""
    heartbeat_prompt: str = "Heartbeat check. Reply HEARTBEAT_OK if no user-facing update is needed."
    heartbeat_sentinel: str = "HEARTBEAT_OK"
    cron_jobs_path: str = ""
    enable_subagents: bool = True
    subagent_max_depth: int = Field(default=1, ge=0)
    enable_worktree_isolation: bool = False
    worktree_dir: str = ".agent/worktrees"

    def to_runtime_config(self, *, system_prompt: str = "") -> RuntimeConfig:
        return RuntimeConfig(
            model=self.model,
            system_prompt=system_prompt,
            max_iterations=self.max_iterations,
            model_timeout_s=self.model_timeout_s,
            stream=self.stream,
            session_id=self.session_id,
            data_dir=self.data_dir,
            context_max_tokens=self.context_max_tokens,
            context_keep_recent=self.context_keep_recent,
            context_tool_result_max_chars=self.context_tool_result_max_chars,
            memory_path=self.memory_path,
            enable_memory=self.enable_memory,
            enable_todos=self.enable_todos,
        )

    def resolved_model_profiles(self) -> list[ModelProfile]:
        profiles = list(self.model_profiles)
        if not profiles:
            profiles.append(ModelProfile(name="primary", model=self.model))
        for index, model in enumerate(_split_csv(self.fallback_models), start=1):
            profiles.append(ModelProfile(name=f"fallback-{index}", model=model))
        return profiles


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    agent_section = loaded.get("agent")
    return agent_section if isinstance(agent_section, dict) else loaded


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class ConfigLoader:
    """从文件系统和 CLI 覆盖项三层合并构建 AgentSettings。

    合并优先级（后者覆盖前者）：
      用户级 TOML → 项目级 TOML → CLI 覆盖项

    TOML 文件格式：顶级字段直接对应 AgentSettings 的字段名；
    也支持将所有字段放在 [agent] section 下。
    """

    def __init__(
        self,
        *,
        project_dir: Path | str,
        user_config_path: Path | str | None = None,
        project_config_path: Path | str | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.user_config_path = (
            Path(user_config_path).expanduser()
            if user_config_path is not None
            else Path.home() / ".zx-code" / "config.toml"
        )
        self.project_config_path = (
            Path(project_config_path)
            if project_config_path is not None
            else self.project_dir / ".zx-code" / "config.toml"
        )

    def load(self, cli_overrides: dict[str, Any] | None = None) -> AgentSettings:
        raw: dict[str, Any] = {}
        raw.update(_read_toml(self.user_config_path))
        raw.update(_read_toml(self.project_config_path))
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                raw[key] = value
        return AgentSettings.model_validate(raw)
