"""core.loop — Agent 主循环入口。

``run_task(task, ...)`` 是整个框架的核心驱动函数，实现了经典的
  用户输入 → LLM 推理 → 工具执行 → 再次推理 的 ReAct 循环。

主循环流程：
  1. 从 SessionStore 恢复历史消息（如有）
  2. 追加用户消息，进入 while 循环
  3. 每轮调用 ContextGuard.prepare() 检查/压缩 token 预算
  4. 用 ResilienceRunner.run() 调用 LLM（含超时、限流退避、溢出压缩）
  5. 若 LLM 无工具调用 → 返回 AgentRunResult（正常结束）
  6. 若有工具调用 → 执行 pre_tool_use hook → 工具 → post_tool_use hook
     连续的 concurrency-safe 工具可并发执行，结果仍按模型返回顺序写回
  7. 工具结果写回消息历史，继续下一轮
  8. 超过 max_iterations → 抛出 MaxIterationsExceededError
"""
from __future__ import annotations

from agent.core.context import ContextGuard
from agent.core.tool_executor import ToolCallExecutor
from agent.hooks import HookRunner
from agent.models import (
    RuntimeConfig,
    AgentRunResult,
    AgentState,
    Message,
    ToolResult,
)
from agent.prompt import DEFAULT_SYSTEM_PROMPT
from agent.prompt import SystemPromptBuilder
from agent.providers.base import ModelClient, StreamHandler
from agent.errors import MaxIterationsExceededError
from agent.core.recovery import RecoveryBudget, ResilienceRunner
from agent.state.sessions import SessionStore
from agent.tools.registry import ToolRegistry


async def run_task(
    task: str,
    *,
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    config: RuntimeConfig | None = None,
    stream_handler: StreamHandler | None = None,
    session_store: SessionStore | None = None,
    context_guard: ContextGuard | None = None,
    prompt_builder: SystemPromptBuilder | None = None,
    hook_runner: HookRunner | None = None,
) -> AgentRunResult:
    runtime_config = config or RuntimeConfig()
    system_prompt = runtime_config.system_prompt
    if not system_prompt and prompt_builder is not None:
        system_prompt = prompt_builder.build(runtime_config)
    if not system_prompt:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    state = AgentState(
        system_prompt=system_prompt,
        max_iterations=runtime_config.max_iterations,
        session_id=runtime_config.session_id,
    )
    if session_store is not None:
        state.messages.extend(session_store.rebuild_messages(runtime_config.session_id))

    user_message = Message.user(task)
    state.messages.append(user_message)
    if session_store is not None:
        session_store.append_message(runtime_config.session_id, user_message)

    recovery_budget = RecoveryBudget()

    while state.turn_count < state.max_iterations:
        state.turn_count += 1
        model_messages = (
            await context_guard.prepare(state.messages)
            if context_guard is not None
            else state.messages
        )
        turn = await ResilienceRunner(
            model_client=model_client,
            timeout_s=runtime_config.model_timeout_s,
            recovery_budget=recovery_budget,
            context_guard=context_guard,
        ).run(
            system_prompt=state.system_prompt,
            messages=model_messages,
            tools=tool_registry.schemas(),
            stream_handler=stream_handler if runtime_config.stream else None,
        )
        assistant_message = Message.assistant(
            content=turn.text,
            tool_calls=turn.tool_calls,
        )
        state.messages.append(assistant_message)
        if session_store is not None:
            session_store.append_message(runtime_config.session_id, assistant_message)

        if not turn.tool_calls:
            return AgentRunResult(
                final_text=turn.text,
                iterations=state.turn_count,
                messages=state.messages,
                tool_results=state.tool_results,
                session_id=runtime_config.session_id,
            )

        async def append_tool_result(result: ToolResult) -> None:
            state.tool_results.append(result)
            tool_message = result.to_message()
            state.messages.append(tool_message)
            if session_store is not None:
                session_store.append_message(runtime_config.session_id, tool_message)

        executor = ToolCallExecutor(
            tool_registry=tool_registry,
            hook_runner=hook_runner or HookRunner.empty(),
            session_id=runtime_config.session_id,
        )
        for result in await executor.execute_many(turn.tool_calls):
            await append_tool_result(result)

    raise MaxIterationsExceededError(
        f"agent hit max iterations: {state.max_iterations}"
    )
