from __future__ import annotations

from pathlib import Path

from agent.config import ConfigLoader


def test_config_loader_merges_user_project_and_cli(tmp_path: Path) -> None:
    user_config = tmp_path / "user.toml"
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)

    user_config.write_text(
        """
model = "gpt-user"
max_iterations = 3
session_id = "from-user"
""".strip(),
        encoding="utf-8",
    )
    project_config.write_text(
        """
[agent]
model = "gpt-project"
reasoning_effort = "medium"
session_id = "from-project"
plugin_dirs = [".zx-code/plugins"]
enable_worktree_isolation = true
worktree_dir = ".zx-code/state/worktrees"
debug_log_enabled = true
debug_log_path = ".zx-code/logs/debug.jsonl"
render_markdown = true
markdown_streaming = true

[[agent.mcp_servers]]
name = "fake"
command = "python"
args = ["server.py"]
env = { TOKEN = "x" }

[agent.permission_tools]
bash = "deny"
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(
        project_dir=project_dir,
        user_config_path=user_config,
    ).load({"session_id": "from-cli"})

    assert settings.model.name == "gpt-project"
    assert settings.model.reasoning_effort == "medium"
    assert settings.model.max_iterations == 3
    assert settings.state.session_id == "from-cli"
    assert settings.extensions.plugin_dirs == [".zx-code/plugins"]
    assert settings.worktree.isolation_enabled is True
    assert settings.worktree.dir == ".zx-code/state/worktrees"
    assert settings.debug.log_enabled is True
    assert settings.debug.log_path == ".zx-code/logs/debug.jsonl"
    assert settings.model.render_markdown is True
    assert settings.model.markdown_streaming is True
    assert settings.permissions.tools == {"bash": "deny"}
    assert settings.extensions.mcp_servers[0].name == "fake"
    assert settings.extensions.mcp_servers[0].command == "python"
    assert settings.extensions.mcp_servers[0].args == ["server.py"]
    assert settings.extensions.mcp_servers[0].env == {"TOKEN": "x"}


def test_config_loader_reads_model_profiles(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent]
model = "gpt-default"
fallback_models = "gpt-fallback-cli"

[[agent.model_profiles]]
name = "primary"
model = "gpt-primary"
api_key_env = "PRIMARY_KEY"

[[agent.model_profiles]]
name = "backup"
model = "gpt-backup"
api_key_env = "BACKUP_KEY"
reasoning_effort = "high"
extra_kwargs = { base_url = "https://example.invalid" }
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()
    profiles = settings.resolved_model_profiles()

    assert [profile.name for profile in profiles] == [
        "primary",
        "backup",
        "fallback-1",
    ]
    assert [profile.model for profile in profiles] == [
        "gpt-primary",
        "gpt-backup",
        "gpt-fallback-cli",
    ]
    assert profiles[1].api_key_env == "BACKUP_KEY"
    assert profiles[1].reasoning_effort == "high"
    assert profiles[1].extra_kwargs == {"base_url": "https://example.invalid"}


def test_global_reasoning_effort_applies_to_implicit_profiles(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent]
model = "gpt-primary"
fallback_models = "gpt-backup-a,gpt-backup-b"
reasoning_effort = "low"
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()
    profiles = settings.resolved_model_profiles()

    assert [profile.reasoning_effort for profile in profiles] == ["low", "low", "low"]


def test_config_loader_reads_nested_trace_settings(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent.trace]
enabled = true
dir = ".zx-code/logs/traces"
sanitize = false
html = true
html_include_raw_response = true
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()
    runtime = settings.to_runtime_config()

    assert settings.trace.enabled is True
    assert settings.trace.dir == ".zx-code/logs/traces"
    assert settings.trace.sanitize is False
    assert settings.trace.html is True
    assert settings.trace.html_include_raw_response is True
    assert runtime.trace_enabled is True
    assert runtime.trace_dir == ".zx-code/logs/traces"
    assert runtime.trace_sanitize is False
    assert runtime.trace_html is True
    assert runtime.trace_html_include_raw_response is True
    assert runtime.trace_sanitize is False
    assert runtime.trace_html is True
    assert runtime.trace_html_include_raw_response is True


def test_config_loader_reads_flat_trace_settings(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent]
trace_enabled = true
trace_dir = ".zx-code/logs/traces"
trace_sanitize = false
trace_html = true
trace_html_include_raw_response = true
""".strip(),
        encoding="utf-8",
    )

    runtime = ConfigLoader(project_dir=project_dir).load().to_runtime_config()

    assert runtime.trace_enabled is True
    assert runtime.trace_dir == ".zx-code/logs/traces"


def test_default_runtime_paths_use_zx_code_home(tmp_path: Path) -> None:
    runtime = ConfigLoader(project_dir=tmp_path).load({}).to_runtime_config()

    assert runtime.data_dir == ".zx-code/state"
    assert runtime.memory_path == ".zx-code/state/memory/MEMORY.md"
    assert runtime.debug_log_path == ".zx-code/logs/debug.jsonl"
    assert runtime.trace_dir == ".zx-code/logs/traces"
    assert runtime.trace_sanitize is True
    assert runtime.trace_html is False
    assert runtime.trace_html_include_raw_response is False
