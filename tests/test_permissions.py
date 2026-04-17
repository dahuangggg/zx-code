from __future__ import annotations

import pytest

from agent.permissions import PermissionManager
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
    assert "permission required" in result.content


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
    assert "permission denied" in result.content

