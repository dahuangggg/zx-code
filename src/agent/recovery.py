from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
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


class ContextOverflowError(AgentError):
    pass


class RateLimitError(AgentError):
    pass


class RecoveryBudget:
    """Track per-category retry attempts to prevent infinite loops."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max = max_retries
        self.attempts: dict[str, int] = {
            "continuation": 0,
            "compaction": 0,
            "backoff": 0,
        }

    def can_retry(self, category: str) -> bool:
        return self.attempts.get(category, 0) < self.max

    def record(self, category: str) -> None:
        self.attempts[category] = self.attempts.get(category, 0) + 1

    def backoff_delay(self) -> float:
        return min(2 ** self.attempts.get("backoff", 0) + random.random(), 60.0)


SleepFunc = Callable[[float], Awaitable[None]]


def classify_error(exc: Exception) -> str:
    """Classify an LLM error for recovery routing."""
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    if isinstance(exc, TimeoutError) or "timeout" in exc_type:
        return "timeout"
    if any(term in msg for term in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"

    if any(term in msg for term in ("rate_limit", "rate limit", "429", "too many requests")):
        return "rate_limit"
    if any(term in msg for term in ("ratelimit",)):
        return "rate_limit"
    if any(term in exc_type for term in ("ratelimit",)):
        return "rate_limit"

    if any(term in msg for term in (
        "invalid api key", "invalid_api_key", "unauthorized", "401",
        "forbidden", "403", "authentication", "permission denied",
    )):
        return "auth"
    if any(term in exc_type for term in ("auth", "permission")):
        return "auth"

    if any(term in msg for term in (
        "billing", "insufficient_quota", "quota exceeded", "payment required",
        "credit", "balance",
    )):
        return "billing"

    if any(term in msg for term in (
        "context_length", "context length", "maximum context",
        "token limit", "too many tokens", "max_tokens",
        "context window", "exceeds the model",
    )):
        return "overflow"

    if any(term in msg for term in ("max_tokens", "length", "truncat")):
        if "finish_reason" in msg or "stop_reason" in msg:
            return "max_tokens"

    return "unknown"


async def run_model_turn_with_recovery(
    model_client: ModelClient,
    *,
    system_prompt: str,
    messages: list[Message],
    tools: list[dict[str, Any]],
    timeout_s: float,
    stream_handler: Callable[[str], Any] | None = None,
    recovery_budget: RecoveryBudget | None = None,
    context_guard: Any | None = None,
) -> ModelTurn:
    runner = ResilienceRunner(
        model_client=model_client,
        timeout_s=timeout_s,
        recovery_budget=recovery_budget,
        context_guard=context_guard,
    )
    return await runner.run(
        system_prompt=system_prompt,
        messages=messages,
        tools=tools,
        stream_handler=stream_handler,
    )


class ResilienceRunner:
    """Run one model turn with bounded recovery strategies."""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        timeout_s: float,
        recovery_budget: RecoveryBudget | None = None,
        context_guard: Any | None = None,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self.model_client = model_client
        self.timeout_s = timeout_s
        self.recovery_budget = recovery_budget or RecoveryBudget()
        self.context_guard = context_guard
        self.sleep = sleep

    async def run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: Callable[[str], Any] | None = None,
    ) -> ModelTurn:
        current_messages = list(messages)

        while True:
            try:
                turn = await asyncio.wait_for(
                    self.model_client.run_turn(
                        system_prompt=system_prompt,
                        messages=current_messages,
                        tools=tools,
                        stream_handler=stream_handler,
                    ),
                    timeout=self.timeout_s,
                )

                if self._should_continue(turn):
                    self.recovery_budget.record("continuation")
                    current_messages = [
                        *current_messages,
                        Message.assistant(turn.text),
                        Message.user(
                            "[Your response was truncated. Please continue from where you left off.]"
                        ),
                    ]
                    continue

                return turn

            except TimeoutError as exc:
                raise ModelTimeoutError(
                    f"model turn timed out after {self.timeout_s:.1f}s"
                ) from exc

            except AgentError:
                raise

            except Exception as exc:
                error_type = classify_error(exc)

                if error_type == "rate_limit" and self.recovery_budget.can_retry("backoff"):
                    self.recovery_budget.record("backoff")
                    await self.sleep(self.recovery_budget.backoff_delay())
                    continue

                if error_type == "overflow" and self.recovery_budget.can_retry("compaction"):
                    self.recovery_budget.record("compaction")
                    if self.context_guard is not None:
                        current_messages = await self.context_guard.compact_history(
                            current_messages
                        )
                        continue
                    raise ContextOverflowError(
                        f"context overflow and no context_guard available: {exc}"
                    ) from exc

                raise ModelInvocationError(f"model turn failed: {exc}") from exc

    def _should_continue(self, turn: ModelTurn) -> bool:
        return (
            turn.stop_reason in ("length", "max_tokens")
            and not turn.tool_calls
            and self.recovery_budget.can_retry("continuation")
        )
