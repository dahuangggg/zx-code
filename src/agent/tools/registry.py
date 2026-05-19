"""tools.registry — 工具路由、权限、熔断和文件写入冲突保护。

ToolRegistry 是 ToolCallExecutor 和具体 Tool 之间的入口。它负责找工具、执行
权限判断、保护写工具不覆盖已变化文件、维护熔断状态，并把所有失败都转换为
ToolResult/ToolEnvelope，避免异常越过工具边界。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.debuglog import DebugLog
from agent.models import ToolEnvelope, ToolError, ToolResult
from agent.permissions import ApprovalCallback, PermissionManager, maybe_await_bool
from agent.tools.base import ErrorCode, Tool
from agent.tools.circuit_breaker import RECOVERY_TIMEOUT_S, CircuitBreaker

_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_PATH_KEYS = ("path", "file_path")


class ToolRegistry:
    """注册并执行工具的运行时目录。

    注册表本身不处理模型消息，也不做输出截断；这些职责分别属于主循环和
    ObservationTruncator。这里的重点是执行前的安全检查和执行后的运行时状态更新。
    """

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
        """注册工具实例；同名工具会导致启动期配置错误。"""
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def active_schemas(self) -> list[dict[str, Any]]:
        """返回当前暴露给模型的工具 schema 列表。

        tool_search 始终保持可见，用来让模型按需发现未激活工具。
        """
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
        """执行单个工具调用，并把所有失败路径规范化为 ToolResult。

        顺序很重要：先确认工具存在，再看熔断，再做写冲突保护，然后权限判断，
        最后才真正执行 Tool.execute()。这样昂贵或有副作用的工具不会在拒绝路径运行。
        """
        tool = self._tools.get(name)
        if tool is None:
            self._debug("tool.registry.unknown", {"tool_name": name, "call_id": call_id})
            return self._error_result(call_id, name, ErrorCode.NOT_FOUND, f"unknown tool: {name}")

        # 熔断发生在权限前：连续失败的工具短时间内直接拒绝，减少重复故障成本。
        if self._circuit_breaker.is_open(name):
            return self._error_result(
                call_id,
                name,
                ErrorCode.CIRCUIT_OPEN,
                f"Tool '{name}' is temporarily disabled after repeated failures. Retry in {RECOVERY_TIMEOUT_S:.0f}s.",
            )

        # 写工具使用“先读后写”的 mtime 检查，避免模型基于旧内容覆盖用户改动。
        if name in _WRITE_TOOL_NAMES:
            conflict = self._check_conflict(name, arguments, call_id)
            if conflict is not None:
                return conflict

        permission = await self._check_permission(name, arguments, call_id)
        if permission is not None:
            return permission

        result = await tool.execute(arguments, call_id=call_id)

        # 熔断只观察实际工具执行结果；权限拒绝和冲突在前面已直接返回。
        if result.is_error:
            self._circuit_breaker.record_failure(name)
        else:
            self._circuit_breaker.record_success(name)

        # read_file 成功后把 mtime 缓存起来，后续 edit/write 可以做乐观锁检查。
        self._maybe_cache_read(result)

        self._debug("tool.registry.executed", {"tool_name": name, "call_id": call_id, "status": result.status})
        return result

    def _check_conflict(
        self, name: str, arguments: dict[str, Any], call_id: str
    ) -> ToolResult | None:
        """检查写工具目标文件是否自上次读取后发生变化。"""
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
        return self._error_result(
            call_id,
            name,
            ErrorCode.CONFLICT,
            f"File '{path_str}' was modified since last read. Re-read before editing.",
        )

    def _maybe_cache_read(self, result: ToolResult) -> None:
        """从 read_file envelope.context 中记录文件 mtime。

        context 由 Tool.execute() 注入原始参数，并由 read_file 放入文件元数据。
        这里直接读 envelope，避免重新解析兼容用的 result.content JSON。
        """
        if result.is_error:
            return
        try:
            ctx = result.envelope.context
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
        """执行权限策略；需要人工批准时调用 approval_callback。"""
        if self.permission_manager is None:
            return None
        check = self.permission_manager.decide(name, arguments)
        self._debug("tool.permission", {"tool_name": name, "call_id": call_id, "decision": check.decision, "reason": check.reason})
        if check.decision == "allow":
            return None
        if check.decision == "deny":
            return self._error_result(call_id, name, ErrorCode.ACCESS_DENIED, f"permission denied: {check.reason}")
        if self.approval_callback is None:
            return self._error_result(call_id, name, ErrorCode.ACCESS_DENIED, f"permission required: {check.reason}")
        approved = await maybe_await_bool(self.approval_callback(check))
        if approved:
            return None
        return self._error_result(call_id, name, ErrorCode.ACCESS_DENIED, f"permission rejected: {check.reason}")

    @staticmethod
    def _error_result(call_id: str, name: str, code: ErrorCode, message: str) -> ToolResult:
        """构造 registry 层错误结果，保持和 Tool.execute() 错误协议一致。"""
        return ToolResult(
            call_id=call_id,
            name=name,
            envelope=ToolEnvelope(
                status="error",
                data={},
                text=message,
                error=ToolError(code=code.value, message=message),
                stats={"time_ms": 0},
                context={"cwd": "."},
            ),
        )

    def _debug(self, event: str, payload: dict[str, Any]) -> None:
        """写入可选 debug log；未启用时 DebugLog.null() 会吞掉事件。"""
        self._log.event(event, payload)
