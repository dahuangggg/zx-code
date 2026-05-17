"""tools.registry — tool routing, permissions, circuit breaker, optimistic locking."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.debuglog import DebugLog
from agent.models import ToolResult
from agent.permissions import ApprovalCallback, PermissionManager, maybe_await_bool
from agent.tools.base import ErrorCode, Tool, build_error_envelope
from agent.tools.circuit_breaker import RECOVERY_TIMEOUT_S, CircuitBreaker

_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_PATH_KEYS = ("path", "file_path")


class ToolRegistry:
    def __init__(
        self,
        *,
        permission_manager: PermissionManager | None = None,
        approval_callback: ApprovalCallback | None = None,
        debug_log: DebugLog | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._active_schema_names: set[str] = set()
        self.permission_manager = permission_manager
        self.approval_callback = approval_callback
        self._log = debug_log or DebugLog.null()
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._read_cache: dict[str, tuple[int, int]] = {}  # abs_path → (mtime_ms, size_bytes)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def active_schemas(self) -> list[dict[str, Any]]:
        names = set(self._active_schema_names)
        if "tool_search" in self._tools:
            names.add("tool_search")
        if not names:
            return self.schemas()
        return [tool.schema() for name, tool in self._tools.items() if name in names]

    def activate_schema(self, name: str) -> None:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        if name != "tool_search":
            self._active_schema_names.add(name)

    def activate_schemas(self, names: list[str]) -> None:
        for name in names:
            self.activate_schema(name)

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
            content = build_error_envelope(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
            return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)

        # Circuit breaker
        if self._circuit_breaker.is_open(name):
            content = build_error_envelope(
                ErrorCode.CIRCUIT_OPEN,
                f"Tool '{name}' is temporarily disabled after repeated failures. Retry in {RECOVERY_TIMEOUT_S:.0f}s.",
            )
            return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)

        # Optimistic locking for write tools
        if name in _WRITE_TOOL_NAMES:
            conflict = self._check_conflict(name, arguments, call_id)
            if conflict is not None:
                return conflict

        permission = await self._check_permission(name, arguments, call_id)
        if permission is not None:
            return permission

        result = await tool.execute(arguments, call_id=call_id)

        # Update circuit breaker
        if result.is_error:
            self._circuit_breaker.record_failure(name)
        else:
            self._circuit_breaker.record_success(name)

        # Cache mtime for read tools
        self._maybe_cache_read(result)

        self._debug("tool.registry.executed", {"tool_name": name, "call_id": call_id, "status": result.status})
        return result

    def _check_conflict(
        self, name: str, arguments: dict[str, Any], call_id: str
    ) -> ToolResult | None:
        path_str = next((arguments.get(k) for k in _PATH_KEYS if arguments.get(k)), None)
        if not path_str:
            return None
        try:
            abs_path = str(Path(path_str).expanduser().resolve())
        except Exception:
            return None
        if abs_path not in self._read_cache:
            return None
        cached_mtime_ms, _ = self._read_cache[abs_path]
        try:
            current_mtime_ms = int(Path(abs_path).stat().st_mtime * 1000)
        except Exception:
            return None
        if current_mtime_ms == cached_mtime_ms:
            return None
        content = build_error_envelope(
            ErrorCode.CONFLICT,
            f"File '{path_str}' was modified since last read. Re-read before editing.",
        )
        return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)

    def _maybe_cache_read(self, result: ToolResult) -> None:
        if result.is_error:
            return
        try:
            env = json.loads(result.content)
            ctx = env.get("context", {})
            mtime_ms = ctx.get("file_mtime_ms")
            params = ctx.get("params_input") or {}
            path_str = next((params.get(k) for k in _PATH_KEYS if params.get(k)), None)
            if mtime_ms and path_str:
                abs_path = str(Path(path_str).expanduser().resolve())
                size_bytes = int(ctx.get("file_size_bytes") or 0)
                self._read_cache[abs_path] = (int(mtime_ms), size_bytes)
        except Exception:
            pass

    async def _check_permission(
        self, name: str, arguments: dict[str, Any], call_id: str
    ) -> ToolResult | None:
        if self.permission_manager is None:
            return None
        check = self.permission_manager.decide(name, arguments)
        self._debug("tool.permission", {"tool_name": name, "call_id": call_id, "decision": check.decision, "reason": check.reason})
        if check.decision == "allow":
            return None
        if check.decision == "deny":
            content = build_error_envelope(ErrorCode.ACCESS_DENIED, f"permission denied: {check.reason}")
            return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)
        if self.approval_callback is None:
            content = build_error_envelope(ErrorCode.ACCESS_DENIED, f"permission required: {check.reason}")
            return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)
        approved = await maybe_await_bool(self.approval_callback(check))
        if approved:
            return None
        content = build_error_envelope(ErrorCode.ACCESS_DENIED, f"permission rejected: {check.reason}")
        return ToolResult(call_id=call_id, name=name, content=content, status="error", is_error=True)

    def _debug(self, event: str, payload: dict[str, Any]) -> None:
        self._log.event(event, payload)
