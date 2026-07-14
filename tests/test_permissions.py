from __future__ import annotations

import pytest

from agent.config import AgentSettings, ConfigLoader
from agent.permissions import PermissionManager, PermissionRule
from agent.runtime.builder import _build_permission_manager
from agent.security import PolicyCompiler
from agent.tools import build_default_registry


@pytest.mark.asyncio
async def test_dangerous_bash_requires_permission() -> None:
    registry = build_default_registry(permission_manager=PermissionManager())

    result = await registry.execute(
        "bash",
        {"command": "rm -rf /tmp/something"},
        call_id="bash-1",
    )

    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "ACCESS_DENIED"
    assert "permission required" in result.envelope.text


@pytest.mark.asyncio
async def test_configured_deny_blocks_tool(tmp_path) -> None:
    registry = build_default_registry(
        permission_manager=PermissionManager(tool_policies={"read_file": "deny"})
    )

    result = await registry.execute(
        "read_file",
        {"path": str(tmp_path / "missing.txt")},
        call_id="read-1",
    )

    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "ACCESS_DENIED"
    assert "permission denied" in result.envelope.text


def test_workspace_check_rejects_similar_prefix_directory(tmp_path) -> None:
    workspace = tmp_path / "repo"
    sibling = tmp_path / "repo-other"
    workspace.mkdir()
    sibling.mkdir()
    manager = PermissionManager(working_dir=workspace)

    check = manager.decide("write_file", {"path": str(sibling / "output.txt")})

    assert check.decision == "ask"
    assert "outside working directory" in check.reason


def test_configured_allow_cannot_bypass_workspace_root(tmp_path) -> None:
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    manager = PermissionManager(
        working_dir=workspace,
        tool_policies={"write_file": "allow"},
    )

    check = manager.decide("write_file", {"path": str(outside / "output.txt")})

    assert check.decision == "ask"
    assert "outside working directory" in check.reason


def test_workspace_check_cannot_weaken_configured_deny(tmp_path) -> None:
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    manager = PermissionManager(
        working_dir=workspace,
        tool_policies={"write_file": "deny"},
    )

    check = manager.decide("write_file", {"path": str(outside / "output.txt")})

    assert check.decision == "deny"
    assert "configured policy" in check.reason


def test_relative_write_path_is_resolved_from_workspace(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = PermissionManager(working_dir=workspace)

    check = manager.decide("write_file", {"path": "notes/output.txt"})

    assert check.decision == "allow"


def test_workspace_check_rejects_symlink_to_outside(tmp_path) -> None:
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "external"
    link.symlink_to(outside, target_is_directory=True)
    manager = PermissionManager(working_dir=workspace)

    check = manager.decide("edit_file", {"path": str(link / "file.txt")})

    assert check.decision == "ask"
    assert "outside working directory" in check.reason


def test_workspace_check_rejects_parent_traversal_to_nonexistent_target(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = PermissionManager(working_dir=workspace)

    check = manager.decide(
        "write_file",
        {"path": "../outside/not-created/output.txt"},
    )

    assert check.decision == "ask"
    assert "outside working directory" in check.reason


def test_allow_rule_cannot_bypass_workspace_root(tmp_path) -> None:
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    manager = PermissionManager(
        working_dir=workspace,
        rules=[PermissionRule(tool="write_file", path="*", decision="allow")],
    )

    check = manager.decide("write_file", {"path": str(outside / "output.txt")})

    assert check.decision == "ask"
    assert "outside working directory" in check.reason


def test_capability_deny_cannot_be_weakened_by_tool_allow(tmp_path) -> None:
    user_config = tmp_path / "user.toml"
    user_config.write_text(
        '[security]\ncapabilities = { shell = "deny" }\n',
        encoding="utf-8",
    )
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=user_config,
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=tmp_path)
    manager = PermissionManager(
        working_dir=tmp_path,
        tool_policies={"bash": "allow"},
        policy=policy,
    )

    check = manager.decide("bash", {"command": "pwd"})

    assert check.decision == "deny"
    assert "capability shell" in check.reason


def test_read_capability_enforces_effective_read_roots(tmp_path) -> None:
    workspace = tmp_path / "repo"
    allowed = workspace / "src"
    outside = workspace / "private"
    workspace.mkdir()
    allowed.mkdir()
    outside.mkdir()
    user_config = tmp_path / "user.toml"
    user_config.write_text(
        f'[security]\nread_roots = ["{allowed}"]\n',
        encoding="utf-8",
    )
    loaded = ConfigLoader(
        project_dir=workspace,
        user_config_path=user_config,
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=workspace)
    manager = PermissionManager(working_dir=workspace, policy=policy)

    allowed_check = manager.decide("grep", {"path": str(allowed), "pattern": "x"})
    outside_check = manager.decide("read_file", {"path": str(outside / "secret.txt")})

    assert allowed_check.decision == "allow"
    assert outside_check.decision == "ask"
    assert "outside effective read roots" in outside_check.reason


def test_runtime_permission_manager_receives_project_root(tmp_path) -> None:
    manager = _build_permission_manager(AgentSettings(), tmp_path)

    assert manager.working_dir == tmp_path.resolve()


def test_runtime_permission_manager_with_rules_receives_project_root(tmp_path) -> None:
    rules_path = tmp_path / ".zx-code" / "permissions.toml"
    rules_path.parent.mkdir()
    rules_path.write_text(
        '[[rules]]\ntool = "write_file"\npath = "*"\ndecision = "ask"\n',
        encoding="utf-8",
    )

    manager = _build_permission_manager(AgentSettings(), tmp_path)

    assert manager.working_dir == tmp_path.resolve()


def test_policy_mode_ignores_allow_rules_from_project_rules_file(tmp_path) -> None:
    rules_path = tmp_path / ".zx-code" / "permissions.toml"
    rules_path.parent.mkdir()
    rules_path.write_text(
        '[[rules]]\ntool = "memory_append"\ndecision = "allow"\n',
        encoding="utf-8",
    )
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    policy = PolicyCompiler().compile(loaded, workspace_root=tmp_path)

    manager = _build_permission_manager(policy.settings, tmp_path, policy)
    check = manager.decide("memory_append", {"content": "x"})

    assert check.decision == "ask"
    assert "durable local state" in check.reason
