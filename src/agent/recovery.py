from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agent.models import Message, ModelTurn
from agent.providers.base import ModelClient


class AgentError(RuntimeError):
    pass


class ModelTimeoutError(AgentError):
    pass


class ModelInvocationError(AgentError):
    pass


class MaxIterationsExceededError(AgentError):
    pass


async def run_model_turn_with_recovery(
    model_client: ModelClient,
    *,
    system_prompt: str,
    messages: list[Message],
    tools: list[dict[str, Any]],
    timeout_s: float,
    stream_handler: Callable[[str], Any] | None = None,
) -> ModelTurn:
    try:
        return await asyncio.wait_for(
            model_client.run_turn(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                stream_handler=stream_handler,
            ),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise ModelTimeoutError(f"model turn timed out after {timeout_s:.1f}s") from exc
    except AgentError:
        raise
    except Exception as exc:
        raise ModelInvocationError(f"model turn failed: {exc}") from exc

