"""tools.registry — 工具注册表，负责路由、权限检查和错误包装。

``ToolRegistry`` 是工具系统的中心：
  - 存储所有已注册工具（按名称索引）
  - ``schemas()`` 返回所有工具的 JSON Schema 列表，传给 LLM
  - ``execute()`` 执行流程：
      1. 按名称查找工具（未知工具返回错误 ToolResult）
      2. 调用 PermissionManager.decide()
         - allow → 继续
         - deny  → 直接返回错误
         - ask   → 调用 ApprovalCallback（用户审批）
      3. 调用 tool.execute()，捕获 ValidationError 和其他异常

所有工具执行结果（包括错误）均返回 ToolResult，
不会向调用方（loop.py）抛出工具级别的异常。
"""

from __future__ import annotations


import traceback
from typing import Any

from pydantic import ValidationError

from agent.debuglog import DebugLog
from agent.models import ToolResult
from agent.permissions import ApprovalCallback, PermissionManager, maybe_await_bool
from agent.tools.base import Tool


class ToolRegistry:
    def __init__(
        self,
        *,
        permission_manager: PermissionManager | None = None,
        approval_callback: ApprovalCallback | None = None,
        debug_log: DebugLog | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.permission_manager = permission_manager
        self.approval_callback = approval_callback
        self.debug_log = debug_log

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
            self._debug("tool.registry.unknown", {"tool_name": name, "call_id": call_id})
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
            self._debug(
                "tool.registry.validation_error",
                {
                    "tool_name": name,
                    "call_id": call_id,
                    "errors": exc.errors(include_url=False),
                },
            )
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"invalid arguments for {name}: {exc.errors(include_url=False)}",
                is_error=True,
            )
        except Exception as exc:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            self._debug(
                "tool.registry.exception",
                {
                    "tool_name": name,
                    "call_id": call_id,
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                    "traceback": tb,
                },
            )
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"{name} failed ({type(exc).__name__}): {exc}\n{''.join(tb[-3:])}",
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
        self._debug(
            "tool.permission",
            {
                "tool_name": name,
                "call_id": call_id,
                "arguments": arguments,
                "decision": check.decision,
                "reason": check.reason,
            },
        )
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

    def _debug(self, event: str, payload: dict[str, Any]) -> None:
        if self.debug_log is not None:
            self.debug_log.event(event, payload)
