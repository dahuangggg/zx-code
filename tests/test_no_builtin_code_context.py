from __future__ import annotations

import importlib.util
from dataclasses import fields
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.config import AgentSettings
from agent.prompt import SystemPromptBuilder
from agent.runtime.types import RuntimeExtensions
from agent.tools.runtime_deps import ToolRuntimeDeps


def test_builtin_code_context_package_is_removed() -> None:
    assert importlib.util.find_spec("agent.code_context") is None
    assert importlib.util.find_spec("agent.tools.code_context") is None


def test_builtin_code_context_config_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentSettings.model_validate({"code_context": {"enabled": True}})


def test_runtime_has_no_builtin_code_context_dependency() -> None:
    assert "code_context_indexer" not in {field.name for field in fields(RuntimeExtensions)}
    assert "code_context_indexer" not in {field.name for field in fields(ToolRuntimeDeps)}


def test_prompt_routes_code_indexing_to_mcp_or_plugin(tmp_path: Path) -> None:
    prompt = SystemPromptBuilder(project_root=tmp_path).build()

    assert "code_search" not in prompt
    assert "CodeContext" not in prompt
    assert "MCP" in prompt
