"""core.tool_executor — tool call lifecycle execution."""
from __future__ import annotations

import asyncio
from typing import Protocol

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
    ) -> None:
        self.tool_registry = tool_registry
        self.hook_runner = hook_runner or HookRunner.empty()
        self.session_id = session_id

    async def execute_many(self, calls: list[ToolCall]) -> list[ToolResult]:
        results: list[ToolResult] = []
        safe_batch: list[ToolCall] = []

        async def flush_safe_batch() -> None:
            if not safe_batch:
                return
            batch = list(safe_batch)
            safe_batch.clear()
            batch_results = await asyncio.gather(
                *(self._execute_allowed(call) for call in batch)
            )
            for call, result in zip(batch, batch_results, strict=True):
                results.append(result)
                await self._run_post_hook(call, result)

        for call in calls:
            hook_result = await self._run_pre_hook(call)
            if hook_result.denied:
                await flush_safe_batch()
                results.append(
                    ToolResult(
                        call_id=call.id,
                        name=call.name,
                        content=f"[hook denied]: {hook_result.reason}",
                        is_error=True,
                    )
                )
                continue

            if self._is_concurrency_safe(call):
                safe_batch.append(call)
                continue

            await flush_safe_batch()
            result = await self._execute_allowed(call)
            results.append(result)
            await self._run_post_hook(call, result)

        await flush_safe_batch()
        return results

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

    async def _execute_allowed(self, call: ToolCall) -> ToolResult:
        return await self.tool_registry.execute(
            call.name,
            call.arguments,
            call_id=call.id,
        )

    def _is_concurrency_safe(self, call: ToolCall) -> bool:
        tool = self.tool_registry.get(call.name)
        return tool is not None and tool.is_concurrency_safe(call.arguments)
