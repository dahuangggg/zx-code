"""core.tool_executor — tool call lifecycle execution."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from inspect import isawaitable
from typing import Any, Protocol

from agent.debuglog import DebugLog
from agent.hooks import HookRunner, HookResult
from agent.models import ToolCall, ToolResult
from agent.tools.registry import ToolRegistry


class ToolHookRunner(Protocol):
    async def run(self, event: str, payload: dict) -> HookResult:
        ...


class ToolCallExecutor:
    """Execute tool calls with hooks and concurrency-safe batching."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        hook_runner: ToolHookRunner | None = None,
        session_id: str,
        debug_log: DebugLog | None = None,
        progress_handler: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.hook_runner = hook_runner or HookRunner.empty()
        self.session_id = session_id
        self._log = debug_log or DebugLog.null()
        self.progress_handler = progress_handler

    async def execute_many(self, calls: list[ToolCall]) -> list[ToolResult]:
        # 预分配结果槽位，确保最终顺序与 calls 输入顺序严格一致，
        # 即使并发执行或 hook 拒绝也不会导致 tool_call_id 错配
        results: list[ToolResult | None] = [None] * len(calls)
        # safe_batch 存 (原始索引, ToolCall)，flush 后按索引写回对应槽位
        safe_batch: list[tuple[int, ToolCall]] = []

        async def flush_safe_batch() -> None:
            if not safe_batch:
                return
            batch = list(safe_batch)
            safe_batch.clear()
            batch_results = await asyncio.gather(
                *(self._execute_allowed(call) for _, call in batch)
            )
            for (orig_idx, call), result in zip(batch, batch_results, strict=True):
                results[orig_idx] = result
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
                    content=f"[hook denied]: {hook_result.reason}",
                    is_error=True,
                )
                continue

            if self._is_concurrency_safe(call):
                safe_batch.append((idx, call))
                continue

            await flush_safe_batch()
            result = await self._execute_allowed(call)
            results[idx] = result
            await self._run_post_hook(call, result)

        await flush_safe_batch()
        # 所有槽位此时应已填满；None 仅在逻辑错误时出现，断言保护
        assert all(r is not None for r in results), "BUG: unfilled tool result slot"
        return results  # type: ignore[return-value]

    async def _run_pre_hook(self, call: ToolCall) -> HookResult:
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
        await self._emit_progress(
            "tool.end",
            {
                "tool_name": call.name,
                "arguments": call.arguments,
                "call_id": call.id,
                "is_error": result.is_error,
            },
        )
        self._log.event(
            "tool.call.result",
            {"call": call, "result": result},
            session_id=self.session_id,
            level="info",
        )
        return result

    def _is_concurrency_safe(self, call: ToolCall) -> bool:
        tool = self.tool_registry.get(call.name)
        return tool is not None and tool.is_concurrency_safe(call.arguments)

    async def _emit_progress(self, event: str, payload: dict[str, Any]) -> None:
        if self.progress_handler is None:
            return
        result = self.progress_handler(event, payload)
        if isawaitable(result):
            await result
