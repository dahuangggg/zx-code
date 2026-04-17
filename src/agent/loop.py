from __future__ import annotations

from agent.context import ContextGuard
from agent.models import AgentConfig, AgentRunResult, AgentState, Message
from agent.prompt import DEFAULT_SYSTEM_PROMPT
from agent.prompt import SystemPromptBuilder
from agent.providers.base import ModelClient, StreamHandler
from agent.recovery import MaxIterationsExceededError, run_model_turn_with_recovery
from agent.sessions import SessionStore
from agent.tools.registry import ToolRegistry


async def run_task(
    task: str,
    *,
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    config: AgentConfig | None = None,
    stream_handler: StreamHandler | None = None,
    session_store: SessionStore | None = None,
    context_guard: ContextGuard | None = None,
    prompt_builder: SystemPromptBuilder | None = None,
) -> AgentRunResult:
    runtime_config = config or AgentConfig()
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

    while state.turn_count < state.max_iterations:
        state.turn_count += 1
        model_messages = (
            context_guard.prepare(state.messages)
            if context_guard is not None
            else state.messages
        )
        turn = await run_model_turn_with_recovery(
            model_client,
            system_prompt=state.system_prompt,
            messages=model_messages,
            tools=tool_registry.schemas(),
            timeout_s=runtime_config.model_timeout_s,
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

        for call in turn.tool_calls:
            result = await tool_registry.execute(
                call.name,
                call.arguments,
                call_id=call.id,
            )
            state.tool_results.append(result)
            tool_message = result.to_message()
            state.messages.append(tool_message)
            if session_store is not None:
                session_store.append_message(runtime_config.session_id, tool_message)

    raise MaxIterationsExceededError(
        f"agent hit max iterations: {state.max_iterations}"
    )
