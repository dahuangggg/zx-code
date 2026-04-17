from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from agent.models import Message, ModelTurn

StreamHandler = Callable[[str], Awaitable[None] | None]


class ModelClient(Protocol):
    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: StreamHandler | None = None,
    ) -> ModelTurn:
        ...

