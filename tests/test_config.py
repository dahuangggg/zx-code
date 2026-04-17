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
    assert settings.permission_tools == {"bash": "deny"}

