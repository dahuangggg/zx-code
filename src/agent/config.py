from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import AgentConfig
from agent.permissions import PermissionDecision


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "openai/gpt-4o-mini"
    max_iterations: int = 8
    model_timeout_s: float = 60.0
    stream: bool = True
    session_id: str = "default"
    data_dir: str = ".agent"
    context_max_chars: int = 40000
    context_keep_recent: int = 24
    context_tool_result_max_chars: int = 6000
    memory_path: str = ".memory/MEMORY.md"
    enable_memory: bool = True
    enable_todos: bool = True
    permission_default: PermissionDecision = "allow"
    permission_tools: dict[str, PermissionDecision] = Field(default_factory=dict)

    def to_agent_config(self, *, system_prompt: str = "") -> AgentConfig:
        return AgentConfig(
            model=self.model,
            system_prompt=system_prompt,
            max_iterations=self.max_iterations,
            model_timeout_s=self.model_timeout_s,
            stream=self.stream,
            session_id=self.session_id,
            data_dir=self.data_dir,
            context_max_chars=self.context_max_chars,
            context_keep_recent=self.context_keep_recent,
            context_tool_result_max_chars=self.context_tool_result_max_chars,
            memory_path=self.memory_path,
            enable_memory=self.enable_memory,
            enable_todos=self.enable_todos,
        )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    agent_section = loaded.get("agent")
    return agent_section if isinstance(agent_section, dict) else loaded


class ConfigLoader:
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

