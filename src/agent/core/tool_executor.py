"""core.tool_executor — 执行模型请求的工具调用。

本模块连接 LLM tool_call、ToolRegistry、hook、进度事件、截断和 debug trace。
它不理解具体工具的业务语义，只维护调用顺序、call_id 对齐和失败隔离。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from inspect import isawaitable
from pathlib import Path
from typing import Any, Protocol

from agent.debuglog import DebugLog
from agent.hooks import HookRunner, HookResult
from agent.models import ToolCall, ToolEnvelope, ToolError, ToolMetric, ToolResult
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
        progress_handler: Callable[[str, dict[str, Any]], Any] | None = None,
        project_root: Path | str | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.hook_runner = hook_runner or HookRunner.empty()
        self.session_id = session_id
        self._log = debug_log or DebugLog.null()
        self.progress_handler = progress_handler
        self._truncator = ObservationTruncator(project_root)

    async def execute_many(
        self, calls: list[ToolCall]
    ) -> tuple[list[ToolResult], list[ToolMetric]]:
        """执行工具调用列表，返回 (results, metrics)。"""
        # 预分配结果槽位，确保最终顺序与 calls 输入顺序严格一致，
        # 即使并发执行或 hook 拒绝也不会导致 tool_call_id 错配
        results: list[ToolResult | None] = [None] * len(calls)
        metrics: list[ToolMetric] = []
        # safe_batch 存 (原始索引, ToolCall)，flush 后按索引写回对应槽位
        safe_batch: list[tuple[int, ToolCall]] = []

        async def flush_safe_batch() -> None:
            """并发执行已收集的安全工具，并把结果写回原始位置。"""
            if not safe_batch:
                return
            batch = list(safe_batch)
            safe_batch.clear()
            starts = [time.monotonic()] * len(batch)
            batch_results = await asyncio.gather(
                *(self._execute_allowed(call) for _, call in batch)
            )
            elapsed = time.monotonic()
            for i, ((orig_idx, call), result) in enumerate(
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
            self._log.event(
                "tool.call.requested",
                {"call": call},
                session_id=self.session_id,
                level="info",
            )
            hook_result = await self._run_pre_hook(call)
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
                # hook deny 是执行链外部拒绝，不经过具体 Tool.run()，因此这里直接
                # 构造 error envelope，仍然保持 ToolResult 的 call_id/name 配对。
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
                results[idx] = ToolResult(
                    call_id=call.id,
                    name=call.name,
                    envelope=ToolEnvelope(
                        status="error",
                        data={},
                        text=f"[hook denied]: {hook_result.reason}",
                        error=ToolError(
                            code="ACCESS_DENIED",
                            message=f"[hook denied]: {hook_result.reason}",
                        ),
                    ),
                )
                continue

            if self._is_concurrency_safe(call):
                safe_batch.append((idx, call))
                continue

            await flush_safe_batch()
            t0 = time.monotonic()
            result = await self._execute_allowed(call)
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
        self._log.event(
            "tool.hook.post",
            {
                "tool_name": call.name,
                "is_error": result.is_error,
                "result": result.content,
            },
            session_id=self.session_id,
        )

    async def _execute_allowed(self, call: ToolCall) -> ToolResult:
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
        result = await self.tool_registry.execute(
            call.name,
            call.arguments,
            call_id=call.id,
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
        return result

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
