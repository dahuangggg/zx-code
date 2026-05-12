from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from agent.config.sections import AgentSettings


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    agent_section = loaded.get("agent")
    return agent_section if isinstance(agent_section, dict) else loaded


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


class ConfigLoader:
    """Load AgentSettings from user, project, and CLI config layers."""

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
        raw = _deep_merge(raw, _read_toml(self.user_config_path))
        raw = _deep_merge(raw, _read_toml(self.project_config_path))
        raw = _deep_merge(
            raw,
            {key: value for key, value in (cli_overrides or {}).items() if value is not None},
        )
        return AgentSettings.model_validate(raw)
