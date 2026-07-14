"""core.loop — Agent 主循环入口。

``run_task(task, ...)`` 是整个框架的核心驱动函数，实现了经典的
  用户输入 → LLM 推理 → 工具执行 → 再次推理 的 ReAct 循环。

主循环流程：
  1. 从 SessionStore 恢复历史消息（如有）
  2. 追加用户消息，进入 while 循环
  3. 每轮调用 ContextGuard.prepare() 检查/压缩 token 预算
  4. 用 ResilienceRunner.run() 调用 LLM（含超时、限流退避、溢出压缩）
  5. 若 LLM 无工具调用 → 返回 AgentRunResult（正常结束）
  6. 若有工具调用 → 先执行完所有工具，再统一落盘 assistant + tool 消息
     （保证崩溃时磁盘状态始终合法：要么全有要么全无）
     连续的 concurrency-safe 工具可并发执行，结果仍按模型返回顺序写回
  7. 工具结果写回消息历史，继续下一轮
  8. 超过 max_iterations → 抛出 MaxIterationsExceededError
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from inspect import isawaitable
from typing import Any

from agent.core.context import ContextGuard
from agent.core.context_assembly import ModelContextAssembler
from agent.core.tool_executor import ToolCallExecutor
from agent.debuglog import DebugLog
from agent.hooks import HookRunner
from agent.models import (
    RuntimeConfig,
    AgentRunResult,
    AgentState,
    Message,
    RunMetrics,
    TokenUsage,
    TurnMetric,
)
from agent.prompt import DEFAULT_SYSTEM_PROMPT
from agent.operations import FileCheckpointStore, OperationJournal
from agent.prompt import SystemPromptBuilder
from agent.providers.base import ModelClient, StreamHandler
from agent.errors import MaxIterationsExceededError
from agent.core.recovery import RecoveryBudget, ResilienceRunner
from agent.state.sessions import SessionStore
from agent.trace import TraceLogger
from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def run_task(
    task: str,
    *,
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    config: RuntimeConfig | None = None,
    stream_handler: StreamHandler | None = None,
    session_store: SessionStore | None = None,
    context_guard: ContextGuard | None = None,
    context_assembler: ModelContextAssembler | None = None,
    prompt_builder: SystemPromptBuilder | None = None,
    hook_runner: HookRunner | None = None,
    debug_log: DebugLog | None = None,
    trace_logger: TraceLogger | None = None,
    progress_handler: Callable[[str, dict[str, Any]], Any] | None = None,
    project_root: Any | None = None,
    operation_journal: OperationJournal | None = None,
    checkpoint_store: FileCheckpointStore | None = None,
) -> AgentRunResult:
    """运行一次 agent 任务直到模型给出最终回答或达到轮次上限。

    这是 framework 的核心循环。调用方负责传入模型客户端和工具注册表；本函数负责
    管理消息历史、上下文压缩、工具执行、session 持久化和运行指标。
    """
    # 规范化：外部传 None 时用 NullDebugLog，内部代码无需再判断 is not None
    _log = debug_log or DebugLog.null()
    _trace = trace_logger or TraceLogger.null()
    runtime_config = config or RuntimeConfig()
    # system_prompt 三级回退：调用方显式传入 → prompt_builder 动态构建 → 框架内置默认值
    system_prompt = runtime_config.system_prompt
    if not system_prompt and prompt_builder is not None:
        system_prompt = prompt_builder.build(runtime_config)
    if not system_prompt:
        system_prompt = DEFAULT_SYSTEM_PROMPT
    _log.event(
        "run.system_prompt",
        {"system_prompt": system_prompt},
        session_id=runtime_config.session_id,
    )
    _trace.log_event(
        "run_start",
        {
            "session_id": runtime_config.session_id,
            "model": runtime_config.model,
            "max_iterations": runtime_config.max_iterations,
        },
        step=0,
    )
    _trace.log_event(
        "system_messages",
        {
            "system_prompt": system_prompt,
            "tool_schemas": tool_registry.active_schemas(),
        },
        step=0,
    )

    state = AgentState(
        system_prompt=system_prompt,
        max_iterations=runtime_config.max_iterations,
        session_id=runtime_config.session_id,
    )
    # 跨进程恢复：从 session_store 重放历史消息，让 agent 接续上一次会话
    if session_store is not None:
        state.messages.extend(session_store.rebuild_messages(runtime_config.session_id))

    user_message = Message.user(task)
    state.messages.append(user_message)
    _log.event(
        "run.user_message",
        {"message": user_message},
        session_id=runtime_config.session_id,
        level="info",
    )
    _trace.log_event(
        "user_input",
        {"message": user_message},
        step=0,
    )
    # 每条消息都立即落盘，崩溃后能从最后一条消息处恢复，不需要等回合结束才持久化
    if session_store is not None:
        session_store.append_message(runtime_config.session_id, user_message)

    recovery_budget = RecoveryBudget()
    run_metrics = RunMetrics()

    async def emit_progress(event: str, payload: dict[str, Any]) -> None:
        if progress_handler is None:
            return
        result = progress_handler(event, payload)
        if isawaitable(result):
            await result

    async def progress_stream_handler(chunk: str) -> None:
        try:
            await emit_progress("model.chunk", {"turn": state.turn_count, "chunk": chunk})
            if stream_handler is not None:
                result = stream_handler(chunk)
                if isawaitable(result):
                    await result
        except Exception as exc:
            # stream_handler 用于展示/进度回调，报错不应中断 LLM 调用本身
            logger.warning("stream_handler raised during chunk output: %s", exc)
            _log.event(
                "stream_handler.error",
                {"error": str(exc), "turn": state.turn_count},
                session_id=runtime_config.session_id,
            )

    try:
        result = await _run_task_loop(
            state=state,
            runtime_config=runtime_config,
            model_client=model_client,
            tool_registry=tool_registry,
            stream_handler=stream_handler,
            session_store=session_store,
            context_guard=context_guard,
            context_assembler=context_assembler,
            hook_runner=hook_runner,
            debug_log=debug_log,
            trace_logger=_trace,
            progress_handler=progress_handler,
            project_root=project_root,
            operation_journal=operation_journal,
            checkpoint_store=checkpoint_store,
            run_metrics=run_metrics,
            recovery_budget=recovery_budget,
            emit_progress=emit_progress,
            progress_stream_handler=progress_stream_handler,
            log=_log,
        )
    except Exception as exc:
        _trace.log_event(
            "error",
            {"type": type(exc).__name__, "message": str(exc)},
            step=state.turn_count,
        )
        _trace.finalize({"metrics": run_metrics, "error": type(exc).__name__})
        raise
    _trace.finalize({"metrics": run_metrics})
    return result


async def _run_task_loop(
    *,
    state: AgentState,
    runtime_config: RuntimeConfig,
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    stream_handler: StreamHandler | None,
    session_store: SessionStore | None,
    context_guard: ContextGuard | None,
    context_assembler: ModelContextAssembler | None,
    hook_runner: HookRunner | None,
    debug_log: DebugLog | None,
    trace_logger: TraceLogger,
    progress_handler: Callable[[str, dict[str, Any]], Any] | None,
    project_root: Any | None,
    operation_journal: OperationJournal | None,
    checkpoint_store: FileCheckpointStore | None,
    run_metrics: RunMetrics,
    recovery_budget: RecoveryBudget,
    emit_progress: Callable[[str, dict[str, Any]], Any],
    progress_stream_handler: Callable[[str], Any],
    log: DebugLog,
) -> AgentRunResult:
    # max_iterations 是硬上限，防止 LLM 与工具陷入无限循环把 token 烧光
    while state.turn_count < state.max_iterations:
        state.turn_count += 1
        # continuation/compaction 预算是轮次内概念，每轮重置；backoff 跨轮累计不重置
        recovery_budget.reset_turn()
        active_tools = tool_registry.active_schemas()
        if context_assembler is not None:
            latest_user = next(
                (message.content for message in reversed(state.messages) if message.role == "user"),
                "",
            )
            context_artifact = await context_assembler.assemble(
                query=latest_user,
                system_prompt=state.system_prompt,
                messages=state.messages,
                tools=active_tools,
            )
            model_messages = context_artifact.messages
            model_system_prompt = context_artifact.system_prompt
            model_tools = context_artifact.tools
        else:
            # 兼容直接调用 run_task 的库用户；正式 runtime 始终注入 assembler。
            model_messages = (
                await context_guard.prepare(state.messages)
                if context_guard is not None
                else state.messages
            )
            model_system_prompt = state.system_prompt
            model_tools = active_tools
            context_artifact = None
        log.event(
            "run.model_input",
            {
                "turn": state.turn_count,
                "messages": list(model_messages),
                "tool_schemas": model_tools,
                "context_accounting": (
                    context_artifact.accounting.model_dump()
                    if context_artifact is not None
                    else None
                ),
                "context_provenance": (
                    [item.model_dump() for item in context_artifact.provenance]
                    if context_artifact is not None
                    else []
                ),
            },
            session_id=runtime_config.session_id,
        )
        trace_logger.log_event(
            "context_build",
            {
                "turn": state.turn_count,
                "message_count": len(model_messages),
                "tool_count": len(model_tools),
                "token_accounting": (
                    context_artifact.accounting.model_dump()
                    if context_artifact is not None
                    else None
                ),
                "provenance": (
                    [item.model_dump() for item in context_artifact.provenance]
                    if context_artifact is not None
                    else []
                ),
            },
            step=state.turn_count,
        )
        await emit_progress("model.start", {"turn": state.turn_count})
        llm_t0 = time.monotonic()
        try:
            turn = await ResilienceRunner(
                model_client=model_client,
                timeout_s=runtime_config.model_timeout_s,
                recovery_budget=recovery_budget,
                # compact_fn 而非 context_guard：ResilienceRunner 只需要压缩能力，
                # 不需要知道 ContextGuard 的其他职责
                compact_fn=context_guard.compact_history if context_guard is not None else None,
            ).run(
                system_prompt=model_system_prompt,
                messages=model_messages,
                tools=model_tools,
                stream_handler=(
                    progress_stream_handler
                    if runtime_config.stream
                    else None
                ),
            )
        finally:
            await emit_progress("model.end", {"turn": state.turn_count})
        llm_duration_ms = max(0.1, round((time.monotonic() - llm_t0) * 1000, 1))
        trace_logger.log_event(
            "model_output",
            {
                "text": turn.text,
                "tool_calls": turn.tool_calls,
                "usage": turn.usage,
                "finish_reason": turn.stop_reason,
            },
            step=state.turn_count,
        )

        assistant_message = Message.assistant(
            content=turn.text,
            tool_calls=turn.tool_calls,
        )

        # 无工具调用即视为最终回答，ReAct 循环在此终止
        if not turn.tool_calls:
            if session_store is not None:
                session_store.append_message(runtime_config.session_id, assistant_message)
            state.messages.append(assistant_message)
            log.event(
                "run.assistant_message",
                {
                    "turn": state.turn_count,
                    "message": assistant_message,
                    "stop_reason": turn.stop_reason,
                },
                session_id=runtime_config.session_id,
                level="info",
            )
            trace_logger.log_event(
                "message_written",
                {"role": "assistant", "message": assistant_message},
                step=state.turn_count,
            )
            run_metrics.record_turn(TurnMetric(
                turn=state.turn_count,
                llm_duration_ms=llm_duration_ms,
                usage=turn.usage or TokenUsage(),
            ))
            log.event(
                "run.metrics",
                {"metrics": run_metrics},
                session_id=runtime_config.session_id,
                level="info",
            )
            trace_logger.log_event(
                "finish",
                {
                    "stop_reason": turn.stop_reason,
                    "final_text": turn.text,
                    "iterations": state.turn_count,
                },
                step=state.turn_count,
            )
            return AgentRunResult(
                final_text=turn.text,
                iterations=state.turn_count,
                messages=state.messages,
                tool_results=state.tool_results,
                session_id=runtime_config.session_id,
                metrics=run_metrics,
            )

        # 有工具调用：先执行完所有工具，再统一落盘 assistant + tool 消息。
        # 顺序保证：若崩溃发生在工具执行期间，磁盘上只有 user 消息，
        # 恢复时状态合法（重新跑 LLM + 工具），不会出现 assistant(tool_calls) 但无 tool 结果的非法状态。
        executor = ToolCallExecutor(
            tool_registry=tool_registry,
            hook_runner=hook_runner or HookRunner.empty(),
            session_id=runtime_config.session_id,
            debug_log=debug_log,
            trace_logger=trace_logger,
            trace_step=state.turn_count,
            progress_handler=progress_handler,
            project_root=project_root,
            operation_journal=operation_journal,
            checkpoint_store=checkpoint_store,
        )
        # execute_many 内部对 concurrency-safe 工具并发执行，但返回顺序与 tool_calls 一致，
        # 保证写回消息历史的顺序与模型调用顺序匹配（否则 tool_call_id 配对会乱）
        tool_results, tool_metrics = await executor.execute_many(turn.tool_calls)

        run_metrics.record_turn(TurnMetric(
            turn=state.turn_count,
            llm_duration_ms=llm_duration_ms,
            usage=turn.usage or TokenUsage(),
            tool_metrics=tool_metrics,
        ))

        # 工具全部执行完毕，开始落盘：assistant 先于 tool，保证协议顺序
        if session_store is not None:
            session_store.append_message(runtime_config.session_id, assistant_message)
        state.messages.append(assistant_message)
        log.event(
            "run.assistant_message",
            {
                "turn": state.turn_count,
                "message": assistant_message,
                "stop_reason": turn.stop_reason,
            },
            session_id=runtime_config.session_id,
            level="info",
        )
        trace_logger.log_event(
            "message_written",
            {"role": "assistant", "message": assistant_message},
            step=state.turn_count,
        )

        for result in tool_results:
            state.tool_results.append(result)
            tool_message = result.to_message()
            if session_store is not None:
                session_store.append_message(runtime_config.session_id, tool_message)
            state.messages.append(tool_message)

    raise MaxIterationsExceededError(
        f"agent hit max iterations: {state.max_iterations}"
    )
