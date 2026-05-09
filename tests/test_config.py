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
session_id = "from-project"
channel = "telegram"
account_id = "bot-a"
dm_scope = "per-channel-peer"
telegram_allowed_chats = "123,456"
feishu_app_id = "cli_xxx"
feishu_webhook_port = 8787
plugin_dirs = [".zx-code/plugins"]
enable_worktree_isolation = true
worktree_dir = ".agent/worktrees"

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

    assert settings.model == "openai/project"
    assert settings.max_iterations == 3
    assert settings.session_id == "from-cli"
    assert settings.channel == "telegram"
    assert settings.account_id == "bot-a"
    assert settings.dm_scope == "per-channel-peer"
    assert settings.telegram_allowed_chats == "123,456"
    assert settings.feishu_app_id == "cli_xxx"
    assert settings.feishu_webhook_port == 8787
    assert settings.plugin_dirs == [".zx-code/plugins"]
    assert settings.enable_worktree_isolation is True
    assert settings.worktree_dir == ".agent/worktrees"
    assert settings.permission_tools == {"bash": "deny"}
    assert settings.mcp_servers[0].name == "fake"
    assert settings.mcp_servers[0].command == "python"
    assert settings.mcp_servers[0].args == ["server.py"]
    assert settings.mcp_servers[0].env == {"TOKEN": "x"}


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
    assert profiles[1].extra_kwargs == {"base_url": "https://example.invalid"}
