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
model = "openai/user"
max_iterations = 3
session_id = "from-user"
""".strip(),
        encoding="utf-8",
    )
    project_config.write_text(
        """
[agent]
model = "openai/project"
reasoning_effort = "medium"
session_id = "from-project"
channel = "telegram"
account_id = "bot-a"
dm_scope = "per-channel-peer"
telegram_allowed_chats = "123,456"
plugin_dirs = [".zx-code/plugins"]
enable_worktree_isolation = true
worktree_dir = ".agent/worktrees"
debug_log_enabled = true
debug_log_path = ".agent/trace.jsonl"
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

    assert settings.model.name == "openai/project"
    assert settings.model.reasoning_effort == "medium"
    assert settings.model.max_iterations == 3
    assert settings.state.session_id == "from-cli"
    assert settings.channel.name == "telegram"
    assert settings.channel.account_id == "bot-a"
    assert settings.routing.dm_scope == "per-channel-peer"
    assert settings.channel.telegram.allowed_chats == "123,456"
    assert settings.extensions.plugin_dirs == [".zx-code/plugins"]
    assert settings.worktree.isolation_enabled is True
    assert settings.worktree.dir == ".agent/worktrees"
    assert settings.debug.log_enabled is True
    assert settings.debug.log_path == ".agent/trace.jsonl"
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
model = "openai/default"
fallback_models = "openai/fallback-cli"

[[agent.model_profiles]]
name = "primary"
model = "openai/primary"
api_key_env = "PRIMARY_KEY"

[[agent.model_profiles]]
name = "backup"
model = "anthropic/backup"
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
        "openai/primary",
        "anthropic/backup",
        "openai/fallback-cli",
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
model = "openai/primary"
fallback_models = "openai/backup-a,openai/backup-b"
reasoning_effort = "low"
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()
    profiles = settings.resolved_model_profiles()

    assert [profile.reasoning_effort for profile in profiles] == ["low", "low", "low"]


def test_config_loader_reads_nested_channel_settings(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent]
channel = "telegram"

[agent.telegram]
token = "tg-token"
allowed_chats = "123,456"
text_coalesce_s = 2.0
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()

    assert settings.channel.telegram.token == "tg-token"
    assert settings.channel.telegram.allowed_chats == "123,456"
    assert settings.channel.telegram.text_coalesce_s == 2.0


def test_config_loader_keeps_legacy_flat_channel_settings(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        """
[agent]
telegram_token = "tg-token"
telegram_allowed_chats = "123,456"
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(project_dir=project_dir).load()

    assert settings.channel.telegram.token == "tg-token"
    assert settings.channel.telegram.allowed_chats == "123,456"


def test_config_loader_deep_merges_nested_channel_settings(tmp_path: Path) -> None:
    user_config = tmp_path / "user.toml"
    project_dir = tmp_path / "project"
    project_config = project_dir / ".zx-code" / "config.toml"
    project_config.parent.mkdir(parents=True)
    user_config.write_text(
        """
[agent.telegram]
token = "from-user"
timeout_s = 20
""".strip(),
        encoding="utf-8",
    )
    project_config.write_text(
        """
[agent.telegram]
allowed_chats = "123"
""".strip(),
        encoding="utf-8",
    )

    settings = ConfigLoader(
        project_dir=project_dir,
        user_config_path=user_config,
    ).load()

    assert settings.channel.telegram.token == "from-user"
    assert settings.channel.telegram.timeout_s == 20
    assert settings.channel.telegram.allowed_chats == "123"
