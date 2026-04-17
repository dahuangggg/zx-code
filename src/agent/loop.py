from __future__ import annotations

from agent.models import AgentConfig, AgentRunResult, AgentState, Message
from agent.prompt import DEFAULT_SYSTEM_PROMPT
from agent.providers.base import ModelClient, StreamHandler
from agent.recovery import MaxIterationsExceededError, run_model_turn_with_recovery
from agent.tools.registry import ToolRegistry


async def run_task(
    task: str,
    *,
    model_client: ModelClient,
    tool_registry: ToolRegistry,
    config: AgentConfig | None = None,
    stream_handler: StreamHandler | None = None,
) -> AgentRunResult:
    runtime_config = config or AgentConfig()
    state = AgentState(
        system_prompt=runtime_config.system_prompt or DEFAULT_SYSTEM_PROMPT,
        max_iterations=runtime_config.max_iterations,
    )
    state.messages.append(Message.user(task))

    while state.turn_count < state.max_iterations:
        state.turn_count += 1
        turn = await run_model_turn_with_recovery(
            model_client,
            system_prompt=state.system_prompt,
            messages=state.messages,
            tools=tool_registry.schemas(),
            timeout_s=runtime_config.model_timeout_s,
            stream_handler=stream_handler if runtime_config.stream else None,
        )
        state.messages.append(
            Message.assistant(
                content=turn.text,
                tool_calls=turn.tool_calls,
            )
        )

        if not turn.tool_calls:
            return AgentRunResult(
                final_text=turn.text,
                iterations=state.turn_count,
                messages=state.messages,
                tool_results=state.tool_results,
            )

        for call in turn.tool_calls:
            result = await tool_registry.execute(
                call.name,
                call.arguments,
                call_id=call.id,
            )
            state.tool_results.append(result)
            state.messages.append(result.to_message())

    raise MaxIterationsExceededError(
        f"agent hit max iterations: {state.max_iterations}"
    )

