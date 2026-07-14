"""长生命周期 AgentSession：session owns resources，run owns turn state。"""

from __future__ import annotations

import asyncio
from inspect import isawaitable
from pathlib import Path
from typing import Any

from agent.config import AgentSettings
from agent.core.loop import run_task
from agent.operations import CheckpointConflict
from agent.runtime.builder import (
    CLIProgressReporter,
    _attach_mcp_tools,
    _build_trace_logger,
    _build_runtime,
    _refresh_system_prompt,
)
from agent.runtime.types import AgentRuntime
from agent.runtime.utils import console
from agent.security import EffectivePolicy
from agent.state.sessions import SessionRecord


class SessionClosedError(RuntimeError):
    pass


class AgentSession:
    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self._run_lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None
        self._closing = False
        self._closed = False

    @classmethod
    async def open(
        cls,
        settings: AgentSettings,
        *,
        session_id: str | None = None,
        lane_scheduler: Any | None = None,
        subagent_depth: int = 0,
        policy: EffectivePolicy | None = None,
    ) -> "AgentSession":
        runtime = _build_runtime(
            settings,
            session_id=session_id,
            lane_scheduler=lane_scheduler,
            subagent_depth=subagent_depth,
            policy=policy,
        )
        await _attach_mcp_tools(runtime, runtime.settings)
        session = cls(runtime)
        session._append_lifecycle(
            "session_opened",
            {
                "schema_version": 1,
                "trust": runtime.policy.trust if runtime.policy is not None else None,
                "unresolved_operations": [
                    record.operation_id
                    for record in runtime.services.operation_journal.records()
                    if record.status == "unknown"
                ],
            },
        )
        return session

    async def __aenter__(self) -> "AgentSession":
        if self._closed:
            raise SessionClosedError("session is closed")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def run(self, task: str) -> Any:
        if self._closing or self._closed:
            raise SessionClosedError("session is closed")
        async with self._run_lock:
            if self._closing or self._closed:
                raise SessionClosedError("session is closed")
            self._active_task = asyncio.current_task()
            runtime = self.runtime
            stream_output = runtime.services.stream_output
            progress_reporter = (
                CLIProgressReporter(console, flush_output=stream_output.flush)
                if runtime.settings.model.stream
                else None
            )
            _refresh_system_prompt(runtime)
            run_trace_logger = _build_trace_logger(
                runtime.settings,
                runtime.project_root,
            )
            try:
                result = await run_task(
                    task,
                    model_client=runtime.services.model_client,
                    tool_registry=runtime.tool_registry,
                    config=runtime.config,
                    stream_handler=stream_output.handler,
                    session_store=runtime.session_store,
                    context_guard=runtime.services.context_guard,
                    context_assembler=getattr(runtime.services, "context_assembler", None),
                    prompt_builder=runtime.services.prompt_builder,
                    hook_runner=runtime.services.hook_runner,
                    debug_log=runtime.services.debug_log,
                    trace_logger=run_trace_logger,
                    project_root=runtime.project_root,
                    progress_handler=(
                        progress_reporter.handle
                        if progress_reporter is not None
                        else None
                    ),
                    operation_journal=runtime.services.operation_journal,
                    checkpoint_store=runtime.services.checkpoint_store,
                )
            finally:
                if progress_reporter is not None:
                    progress_reporter.stop()
                stream_output.flush()
                self._active_task = None
            if (
                runtime.settings.model.stream
                and result.final_text
                and stream_output.renderer is None
            ):
                console.print()
            self._append_lifecycle(
                "run_completed",
                {
                    "schema_version": 1,
                    "iterations": getattr(result, "iterations", None),
                    "operation_ids": [
                        tool_result.envelope.context.get("operation_id")
                        for tool_result in result.tool_results
                        if tool_result.envelope.context.get("operation_id")
                    ],
                },
            )
            return result

    def undo(self, operation_id: str | None = None) -> tuple[str, Path]:
        """撤销指定文件操作；未指定时撤销最近一次仍可撤销的修改。"""
        if self._closing or self._closed:
            raise SessionClosedError("session is closed")
        store = self.runtime.services.checkpoint_store
        checkpoint = store.get(operation_id) if operation_id else store.latest_finalized()
        policy = self.runtime.policy
        if policy is not None and policy.decide("write_fs").state == "deny":
            raise CheckpointConflict("undo denied by effective write_fs policy")

        target = Path(checkpoint.path).resolve(strict=False)
        allowed_roots = (
            policy.write_roots
            if policy is not None
            else (Path(self.runtime.project_root).expanduser().resolve(),)
        )
        if not any(target.is_relative_to(root) for root in allowed_roots):
            raise CheckpointConflict("checkpoint target is outside effective write roots")

        return checkpoint.operation_id, store.undo(checkpoint.operation_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        active = self._active_task
        if active is not None and active is not asyncio.current_task():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)

        team = self.runtime.extensions.team
        if team is not None:
            await team.shutdown()
        router = self.runtime.extensions.mcp_router
        if router is not None:
            await router.close()

        seen: set[int] = set()
        for client in (
            self.runtime.services.model_client,
            self.runtime.services.summary_model_client,
        ):
            await _maybe_close(client, seen)
        self.runtime.services.stream_output.flush()
        self._append_lifecycle("session_closed", {"schema_version": 1})
        self._closed = True

    def _append_lifecycle(self, record_type: str, payload: dict[str, Any]) -> None:
        self.runtime.session_store.append(
            self.runtime.settings.state.session_id,
            SessionRecord(type=record_type, payload=payload),
        )


async def _maybe_close(resource: Any, seen: set[int]) -> None:
    if resource is None or id(resource) in seen:
        return
    seen.add(id(resource))
    close = getattr(resource, "close", None)
    if close is not None:
        result = close()
        if isawaitable(result):
            await result
        return
    for attribute in ("model_client", "_client"):
        child = getattr(resource, attribute, None)
        if child is not None:
            await _maybe_close(child, seen)
