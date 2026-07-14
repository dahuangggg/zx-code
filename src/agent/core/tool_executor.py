"""core.tool_executor — 执行模型请求的工具调用。

本模块连接 LLM tool_call、ToolRegistry、hook、进度事件、截断和 debug trace。
它不理解具体工具的业务语义，只维护调用顺序、call_id 对齐和失败隔离。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from inspect import isawaitable
from pathlib import Path
from typing import Any, Protocol

from agent.debuglog import DebugLog
from agent.hooks import HookRunner, HookResult
from agent.models import ToolCall, ToolEnvelope, ToolError, ToolMetric, ToolResult
from agent.operations import FileCheckpointStore, OperationJournal, OperationRequest
from agent.trace import TraceLogger
from agent.tools.registry import ToolRegistry
from agent.tools.truncator import ObservationTruncator


class ToolHookRunner(Protocol):
    """ToolCallExecutor 需要的 hook runner 最小接口。"""

    async def run(self, event: str, payload: dict) -> HookResult:
        ...


class ToolCallExecutor:
    """执行一轮模型返回的 tool calls。

    execute_many() 保证返回结果顺序和输入 calls 顺序一致。即使部分工具被 hook
    拒绝，或部分工具并发执行，最终 role="tool" 消息仍能按 call_id 精确回填给模型。
    """

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        hook_runner: ToolHookRunner | None = None,
        session_id: str,
        debug_log: DebugLog | None = None,
        trace_logger: TraceLogger | None = None,
        trace_step: int = 0,
        progress_handler: Callable[[str, dict[str, Any]], Any] | None = None,
        project_root: Path | str | None = None,
        operation_journal: OperationJournal | None = None,
        operation_timeout_s: float = 300.0,
        checkpoint_store: FileCheckpointStore | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.hook_runner = hook_runner or HookRunner.empty()
        self.session_id = session_id
        self._log = debug_log or DebugLog.null()
        self._trace = trace_logger or TraceLogger.null()
        self.trace_step = trace_step
        self.progress_handler = progress_handler
        self._truncator = ObservationTruncator(project_root)
        self.operation_journal = operation_journal
        self.operation_timeout_s = operation_timeout_s
        self.checkpoint_store = checkpoint_store

    async def execute_many(
        self, calls: list[ToolCall]
    ) -> tuple[list[ToolResult], list[ToolMetric]]:
        """执行工具调用列表，返回 (results, metrics)。"""
        # 预分配结果槽位，确保最终顺序与 calls 输入顺序严格一致，
        # 即使并发执行或 hook 拒绝也不会导致 tool_call_id 错配
        results: list[ToolResult | None] = [None] * len(calls)
        metrics: list[ToolMetric] = []
        # safe_batch 存 (原始索引, ToolCall)，flush 后按索引写回对应槽位
        safe_batch: list[tuple[int, ToolCall, str]] = []

        async def flush_safe_batch() -> None:
            """并发执行已收集的安全工具，并把结果写回原始位置。"""
            if not safe_batch:
                return
            batch = list(safe_batch)
            safe_batch.clear()
            starts = [time.monotonic()] * len(batch)
            batch_results = await asyncio.gather(
                *(self._execute_allowed(call, operation_id) for _, call, operation_id in batch)
            )
            elapsed = time.monotonic()
            for i, ((orig_idx, call, _operation_id), result) in enumerate(
                zip(batch, batch_results, strict=True)
            ):
                results[orig_idx] = result
                metrics.append(ToolMetric(
                    name=call.name,
                    call_id=call.id,
                    duration_ms=round((elapsed - starts[i]) * 1000, 1),
                    is_error=result.is_error,
                ))
                await self._run_post_hook(call, result)

        for idx, call in enumerate(calls):
            operation_id = self._start_operation(call)
            self._log.event(
                "tool.call.requested",
                {"call": call},
                session_id=self.session_id,
                level="info",
            )
            self._trace_tool_call(call)
            access_error = await self.tool_registry.check_access(
                call.name,
                call.arguments,
                call_id=call.id,
            )
            if access_error is not None:
                access_error = self._with_operation_id(access_error, operation_id)
                self._fail_operation(access_error, operation_id)
                results[idx] = access_error
                await self._emit_terminal_progress(call, access_error)
                self._trace_tool_result(access_error)
                continue
            try:
                hook_result = await self._run_pre_hook(call)
            except Exception as exc:
                result = self._error_result(
                    call,
                    operation_id,
                    "HOOK_ERROR",
                    f"pre-tool hook failed: {type(exc).__name__}",
                )
                self._fail_operation(result, operation_id)
                results[idx] = result
                self._trace_tool_result(result)
                continue
            self._log.event(
                "tool.hook.pre",
                {
                    "tool_name": call.name,
                    "denied": hook_result.denied,
                    "reason": hook_result.reason,
                },
                session_id=self.session_id,
            )
            if hook_result.denied:
                # hook 拒绝来自执行链外部，不经过具体工具实现，因此这里直接
                # 构造错误 envelope，仍然保持 ToolResult 的调用 id/name 配对。
                await flush_safe_batch()
                await self._emit_progress(
                    "tool.start",
                    {
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "call_id": call.id,
                    },
                )
                await self._emit_progress(
                    "tool.end",
                    {
                        "tool_name": call.name,
                        "arguments": call.arguments,
                        "call_id": call.id,
                        "is_error": True,
                    },
                )
                results[idx] = self._error_result(
                    call,
                    operation_id,
                    "ACCESS_DENIED",
                    f"[hook denied]: {hook_result.reason}",
                )
                self._fail_operation(results[idx], operation_id)
                self._trace_tool_result(results[idx])
                continue

            if self._is_concurrency_safe(call):
                safe_batch.append((idx, call, operation_id))
                continue

            await flush_safe_batch()
            t0 = time.monotonic()
            result = await self._execute_allowed(call, operation_id)
            metrics.append(ToolMetric(
                name=call.name,
                call_id=call.id,
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                is_error=result.is_error,
            ))
            results[idx] = result
            await self._run_post_hook(call, result)

        await flush_safe_batch()
        # 所有槽位此时应已填满；None 仅在逻辑错误时出现，断言保护
        assert all(r is not None for r in results), "BUG: unfilled tool result slot"
        return results, metrics  # type: ignore[return-value]

    async def _run_pre_hook(self, call: ToolCall) -> HookResult:
        """运行 pre_tool_use hook；hook 可以拒绝本次工具调用。"""
        return await self.hook_runner.run(
            "pre_tool_use",
            {
                "event": "pre_tool_use",
                "tool_name": call.name,
                "arguments": call.arguments,
                "session_id": self.session_id,
            },
        )

    async def _run_post_hook(self, call: ToolCall, result: ToolResult) -> None:
        """运行 post_tool_use hook，并保留旧 hook payload 的 result 字符串。"""
        try:
            await self.hook_runner.run(
                "post_tool_use",
                {
                    "event": "post_tool_use",
                    "tool_name": call.name,
                    "arguments": call.arguments,
                    "result": result.content,
                    "is_error": result.is_error,
                    "session_id": self.session_id,
                },
            )
        except Exception as exc:
            self._log.event(
                "tool.hook.post_error",
                {"tool_name": call.name, "error_type": type(exc).__name__},
                session_id=self.session_id,
            )
        self._log.event(
            "tool.hook.post",
            {
                "tool_name": call.name,
                "is_error": result.is_error,
                "result": result.content,
            },
            session_id=self.session_id,
        )

    async def _execute_allowed(self, call: ToolCall, operation_id: str) -> ToolResult:
        """执行已通过 hook/权限入口的工具，并完成截断与 trace。

        ToolRegistry 返回完整 ToolEnvelope；ObservationTruncator 只在过大时替换成
        partial/error envelope，并把完整 envelope JSON 落盘，模型只看到摘要路径。
        """
        await self._emit_progress(
            "tool.start",
            {
                "tool_name": call.name,
                "arguments": call.arguments,
                "call_id": call.id,
            },
        )
        checkpoint = None
        if self.checkpoint_store is not None:
            try:
                checkpoint = self.checkpoint_store.begin(
                    operation_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
            except Exception as exc:
                result = self._error_result(
                    call,
                    operation_id,
                    "CHECKPOINT_ERROR",
                    f"failed to prepare file checkpoint: {type(exc).__name__}",
                )
                self._fail_operation(result, operation_id)
                await self._emit_terminal_progress(call, result)
                self._trace_tool_result(result)
                return result
        try:
            async with asyncio.timeout(self.operation_timeout_s):
                result = await self.tool_registry.invoke(
                    call.name,
                    call.arguments,
                    call_id=call.id,
                )
        except asyncio.CancelledError:
            if self.operation_journal is not None:
                if _side_effect(call.name) in {"write", "external"}:
                    self.operation_journal.mark_unknown(
                        operation_id,
                        reason="operation cancelled after invocation began",
                    )
                else:
                    self.operation_journal.fail(
                        operation_id,
                        error_code="CANCELLED",
                        message="operation cancelled",
                    )
            raise
        except TimeoutError:
            result = self._error_result(
                call,
                operation_id,
                "TIMEOUT",
                f"operation exceeded deadline of {self.operation_timeout_s:g}s",
            )
            if self.operation_journal is not None:
                if _side_effect(call.name) in {"write", "external"}:
                    self.operation_journal.mark_unknown(
                        operation_id,
                        reason="operation deadline expired after invocation began",
                    )
                else:
                    self._fail_operation(result, operation_id)
        except Exception as exc:
            result = self._error_result(
                call,
                operation_id,
                "EXECUTION_ERROR",
                f"tool adapter failed: {type(exc).__name__}",
            )
            if self.operation_journal is not None:
                if _side_effect(call.name) in {"write", "external"}:
                    self.operation_journal.mark_unknown(
                        operation_id,
                        reason=f"adapter raised after invocation: {type(exc).__name__}",
                    )
                else:
                    self._fail_operation(result, operation_id)
        else:
            result = self._with_operation_id(result, operation_id)
            if result.is_error:
                self._record_tool_error(result, operation_id, call.name)
            elif self.operation_journal is not None:
                evidence = _execution_evidence(call, result)
                if checkpoint is not None and self.checkpoint_store is not None:
                    try:
                        evidence = self.checkpoint_store.finalize(operation_id)
                    except Exception as exc:
                        evidence.update({
                            "checkpoint_error": type(exc).__name__,
                            "checkpoint_status": "prepared",
                        })
                        self._log.event(
                            "operation.checkpoint.finalize_error",
                            {
                                "operation_id": operation_id,
                                "error_type": type(exc).__name__,
                            },
                            session_id=self.session_id,
                        )
                self.operation_journal.commit(
                    operation_id,
                    result=result,
                    evidence=evidence,
                )
        truncated_envelope = self._truncator.truncate(call.name, result.envelope)
        if truncated_envelope != result.envelope:
            result = result.model_copy(update={"envelope": truncated_envelope})

        result_text = result.envelope.text

        await self._emit_progress(
            "tool.end",
            {
                "tool_name": call.name,
                "arguments": call.arguments,
                "call_id": call.id,
                "is_error": result.is_error,
                "result_text": result_text,
            },
        )
        self._log.event(
            "tool.result",
            {
                "call_id": result.call_id,
                "name": result.name,
                "envelope": result.envelope,
            },
            session_id=self.session_id,
            level="info",
        )
        self._trace_tool_result(result)
        return result

    async def _emit_terminal_progress(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> None:
        await self._emit_progress(
            "tool.start",
            {
                "tool_name": call.name,
                "arguments": call.arguments,
                "call_id": call.id,
            },
        )
        await self._emit_progress(
            "tool.end",
            {
                "tool_name": call.name,
                "arguments": call.arguments,
                "call_id": call.id,
                "is_error": result.is_error,
                "result_text": result.envelope.text,
            },
        )

    def _start_operation(self, call: ToolCall) -> str:
        operation_id = uuid.uuid4().hex
        if self.operation_journal is not None:
            self.operation_journal.start(
                OperationRequest(
                    operation_id=operation_id,
                    session_id=self.session_id,
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    capability=_capability(call.name),
                    side_effect=_side_effect(call.name),
                )
            )
        return operation_id

    def _fail_operation(self, result: ToolResult, operation_id: str) -> None:
        if self.operation_journal is None:
            return
        error = result.envelope.error
        self.operation_journal.fail(
            operation_id,
            error_code=error.code if error is not None else "EXECUTION_ERROR",
            message=error.message if error is not None else result.envelope.text,
            result=result,
        )

    def _record_tool_error(
        self,
        result: ToolResult,
        operation_id: str,
        tool_name: str,
    ) -> None:
        if self.operation_journal is None:
            return
        error = result.envelope.error
        error_code = error.code if error is not None else "EXECUTION_ERROR"
        uncertain = error_code in {
            "EXECUTION_ERROR",
            "INTERNAL_ERROR",
            "TIMEOUT",
        }
        if uncertain and _side_effect(tool_name) in {"write", "external"}:
            self.operation_journal.mark_unknown(
                operation_id,
                reason=f"adapter returned uncertain error after invocation: {error_code}",
            )
            return
        self._fail_operation(result, operation_id)

    @staticmethod
    def _with_operation_id(result: ToolResult, operation_id: str) -> ToolResult:
        context = dict(result.envelope.context)
        context["operation_id"] = operation_id
        return result.model_copy(
            update={"envelope": result.envelope.model_copy(update={"context": context})}
        )

    def _error_result(
        self,
        call: ToolCall,
        operation_id: str,
        code: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            name=call.name,
            envelope=ToolEnvelope(
                status="error",
                data={},
                text=message,
                error=ToolError(code=code, message=message),
                context={"cwd": ".", "operation_id": operation_id},
            ),
        )

    def _is_concurrency_safe(self, call: ToolCall) -> bool:
        """询问具体工具是否允许在同一轮中和其他安全工具并发。"""
        tool = self.tool_registry.get(call.name)
        return tool is not None and tool.is_concurrency_safe(call.arguments)

    async def _emit_progress(self, event: str, payload: dict[str, Any]) -> None:
        """向 CLI/UI 汇报工具状态；同步和异步 handler 都支持。"""
        if self.progress_handler is None:
            return
        result = self.progress_handler(event, payload)
        if isawaitable(result):
            await result

    def _trace_tool_call(self, call: ToolCall) -> None:
        self._trace.log_event(
            "tool_call",
            {
                "tool": call.name,
                "tool_call_id": call.id,
                "args": call.arguments,
            },
            step=self.trace_step,
        )

    def _trace_tool_result(self, result: ToolResult) -> None:
        self._trace.log_event(
            "tool_result",
            {
                "tool": result.name,
                "tool_call_id": result.call_id,
                "result": result.envelope,
            },
            step=self.trace_step,
        )


_READ_TOOLS = frozenset({"read_file", "grep", "glob"})
_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "memory_append",
        "todo_create",
        "todo_update",
        "todo_complete",
        "task_create",
        "task_update",
        "task_complete",
        "task_cancel",
        "worktree_create",
        "worktree_cleanup",
    }
)


def _side_effect(tool_name: str) -> str:
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _WRITE_TOOLS:
        return "write"
    if tool_name == "tool_search":
        return "none"
    return "external"


def _capability(tool_name: str) -> str | None:
    if tool_name in _READ_TOOLS:
        return "read_fs"
    if tool_name in _WRITE_TOOLS:
        return "write_fs"
    if tool_name == "bash":
        return "shell"
    if tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name.startswith("plugin__"):
        return "plugin"
    return None


def _execution_evidence(call: ToolCall, result: ToolResult) -> dict[str, Any]:
    if call.name != "bash":
        return {}
    exit_code = result.envelope.data.get("exit_code")
    command = call.arguments.get("command", "")
    return {"verification": f"{command} (exit {exit_code})"}
