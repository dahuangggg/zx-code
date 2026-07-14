from __future__ import annotations

from pathlib import Path

from agent.config import ConfigLoader
from agent.security import PolicyCompiler


def _load(
    tmp_path: Path,
    *,
    user: str = "",
    project: str = "",
    cli: dict[str, object] | None = None,
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    user_path = tmp_path / "user.toml"
    if user:
        user_path.write_text(user, encoding="utf-8")
    project_path = project_dir / ".zx-code" / "config.toml"
    if project:
        project_path.parent.mkdir()
        project_path.write_text(project, encoding="utf-8")
    loaded = ConfigLoader(
        project_dir=project_dir,
        user_config_path=user_path,
    ).load_with_sources(cli)
    return loaded, project_dir


def test_untrusted_project_cannot_widen_authority_capabilities(tmp_path: Path) -> None:
    loaded, project_dir = _load(
        tmp_path,
        user="""
[security]
workspace_trust = "untrusted"
capabilities = { shell = "ask", network = "deny", mcp = "deny" }
""",
        project="""
[agent.security]
capabilities = { shell = "allow", network = "allow", mcp = "allow" }
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.trust == "untrusted"
    assert policy.decide("shell").state == "ask"
    assert policy.decide("network").state == "deny"
    assert policy.decide("mcp").state == "deny"
    assert len(policy.diagnostics) == 3
    assert all(item.code == "PROJECT_CAPABILITY_WIDENING_IGNORED" for item in policy.diagnostics)


def test_project_may_tighten_capabilities(tmp_path: Path) -> None:
    loaded, project_dir = _load(
        tmp_path,
        user="""
[security]
workspace_trust = "trusted"
capabilities = { shell = "allow", network = "ask", plugin = "allow" }
""",
        project="""
[agent.security]
capabilities = { shell = "ask", network = "deny", plugin = "deny" }
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.trust == "trusted"
    assert policy.decide("shell").state == "ask"
    assert policy.decide("network").state == "deny"
    assert policy.decide("plugin").state == "deny"
    assert policy.diagnostics == ()


def test_project_cannot_expand_authority_roots(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    allowed = project_dir / "src"
    outside = tmp_path / "outside"
    loaded, project_dir = _load(
        tmp_path,
        user=f"""
[security]
read_roots = ["{allowed}"]
write_roots = ["{allowed}"]
""",
        project=f"""
[agent.security]
read_roots = ["{allowed}", "{outside}"]
write_roots = ["{outside}"]
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.read_roots == (allowed.resolve(),)
    assert policy.write_roots == ()
    assert [item.code for item in policy.diagnostics] == [
        "PROJECT_ROOT_WIDENING_IGNORED",
        "PROJECT_ROOT_WIDENING_IGNORED",
    ]


def test_cli_is_authority_and_overrides_user_security_policy(tmp_path: Path) -> None:
    loaded, project_dir = _load(
        tmp_path,
        user="""
[security]
workspace_trust = "untrusted"
capabilities = { shell = "deny" }
""",
        cli={
            "security": {
                "workspace_trust": "trusted",
                "capabilities": {"shell": "allow"},
            }
        },
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.trust == "trusted"
    assert policy.decide("shell").state == "allow"
    assert policy.decide("shell").sources == ("default", "user", "cli")


def test_untrusted_defaults_disable_external_extension_startup(tmp_path: Path) -> None:
    loaded, project_dir = _load(tmp_path)

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.trust == "untrusted"
    assert policy.decide("mcp").state == "deny"
    assert policy.decide("plugin").state == "deny"
    assert policy.decide("hook").state == "deny"
    assert policy.read_roots == (project_dir.resolve(),)
    assert policy.write_roots == (project_dir.resolve(),)


def test_project_cannot_replace_authority_model_endpoints_or_api_key_env(tmp_path: Path) -> None:
    loaded, project_dir = _load(
        tmp_path,
        user="""
[[model.profiles]]
name = "primary"
model = "gpt-user"
api_key_env = "USER_MODEL_KEY"
extra_kwargs = { base_url = "https://model.user.example/v1" }
""",
        project="""
[[agent.model.profiles]]
name = "project"
model = "gpt-project"
api_key_env = "PROJECT_MODEL_KEY"
extra_kwargs = { base_url = "https://model.project.invalid/v1" }
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.settings.model.profiles[0].name == "primary"
    assert policy.settings.model.profiles[0].extra_kwargs["base_url"] == "https://model.user.example/v1"
    assert [item.code for item in policy.diagnostics].count("PROJECT_ENDPOINT_IGNORED") == 1


def test_project_permission_allow_cannot_override_authority_deny(tmp_path: Path) -> None:
    loaded, project_dir = _load(
        tmp_path,
        user="""
[permissions]
default = "ask"
tools = { bash = "deny" }
""",
        project="""
[agent.permissions]
default = "allow"
tools = { bash = "allow", read_file = "allow" }
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.settings.permissions.default == "ask"
    assert policy.settings.permissions.tools == {"bash": "deny"}
    assert [item.code for item in policy.diagnostics].count("PROJECT_PERMISSION_WIDENING_IGNORED") == 3


def test_project_runtime_paths_cannot_escape_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    loaded, project_dir = _load(
        tmp_path,
        project=f"""
[agent.state]
data_dir = "{outside / 'state'}"
memory_path = "../outside/MEMORY.md"

[agent.permissions]
rules_path = "{outside / 'permissions.toml'}"

[agent.extensions]
hooks_path = "{outside / 'hooks.toml'}"
plugin_dirs = [".zx-code/plugins", "{outside / 'plugins'}"]
""",
    )

    policy = PolicyCompiler().compile(loaded, workspace_root=project_dir)

    assert policy.settings.state.data_dir == ".zx-code/state"
    assert policy.settings.state.memory_path == ".zx-code/state/memory/MEMORY.md"
    assert policy.settings.permissions.rules_path == ""
    assert policy.settings.extensions.hooks_path == ""
    assert policy.settings.extensions.plugin_dirs == [".zx-code/plugins"]
    assert [item.code for item in policy.diagnostics].count("PROJECT_PATH_ESCAPE_IGNORED") == 5
