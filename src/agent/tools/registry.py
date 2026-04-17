from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent.models import ToolResult
from agent.permissions import ApprovalCallback, PermissionManager, maybe_await_bool
from agent.tools.base import Tool


class ToolRegistry:
    def __init__(
        self,
        *,
        permission_manager: PermissionManager | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.permission_manager = permission_manager
        self.approval_callback = approval_callback

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"unknown tool: {name}",
                is_error=True,
            )

        permission = await self._check_permission(name, arguments, call_id)
        if permission is not None:
            return permission

        try:
            return await tool.execute(arguments, call_id=call_id)
        except ValidationError as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"invalid arguments for {name}: {exc.errors(include_url=False)}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"{name} failed: {exc}",
                is_error=True,
            )

    async def _check_permission(
        self,
        name: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> ToolResult | None:
        if self.permission_manager is None:
            return None

        check = self.permission_manager.decide(name, arguments)
        if check.decision == "allow":
            return None
        if check.decision == "deny":
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"permission denied: {check.reason}",
                is_error=True,
            )

        if self.approval_callback is None:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"permission required: {check.reason}",
                is_error=True,
            )

        approved = await maybe_await_bool(self.approval_callback(check))
        if approved:
            return None
        return ToolResult(
            call_id=call_id,
            name=name,
            content=f"permission rejected: {check.reason}",
            is_error=True,
        )
