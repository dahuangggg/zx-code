from __future__ import annotations

import json
from pathlib import Path

from agent.config import ConfigLoader
from agent.security import PolicyCompiler, build_doctor_report, render_doctor_report


def test_doctor_report_explains_policy_without_secret_values(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config_path = project / ".zx-code" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
[agent.extensions]
hooks_path = ".zx-code/hooks.toml"
plugin_dirs = [".zx-code/plugins"]

[[agent.extensions.mcp_servers]]
name = "demo"
command = "python"
args = ["server.py"]
env = { TOKEN = "super-secret", OTHER = "also-secret" }
""",
        encoding="utf-8",
    )
    loaded = ConfigLoader(
        project_dir=project,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=project)

    report = build_doctor_report(loaded, policy, workspace_root=project)
    rendered = render_doctor_report(report, json_output=True)
    payload = json.loads(rendered)

    assert payload["workspace"]["trust"] == "untrusted"
    assert payload["capabilities"]["mcp"]["state"] == "deny"
    assert payload["extensions"]["mcp_servers"][0]["command"] == "python"
    assert payload["extensions"]["mcp_servers"][0]["env_keys"] == ["OTHER", "TOKEN"]
    assert "super-secret" not in rendered
    assert "also-secret" not in rendered
    assert payload["sources"][0]["source"] == "project"


def test_human_doctor_report_contains_roots_and_diagnostics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    loaded = ConfigLoader(
        project_dir=project,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=project)

    rendered = render_doctor_report(
        build_doctor_report(loaded, policy, workspace_root=project),
        json_output=False,
    )

    assert "Workspace trust: untrusted" in rendered
    assert "Read roots:" in rendered
    assert "Model endpoints:" in rendered
    assert "External commands:" in rendered
    assert str(project.resolve()) in rendered
    assert "mcp: deny" in rendered
