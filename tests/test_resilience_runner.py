from __future__ import annotations

from typing import Any

from agent.models import Message, ModelTurn
from agent.core.recovery import ResilienceRunner


class _ScriptedClient:
    def __init__(self, results: list[ModelTurn | Exception]) -> None:
        self.results = results
        self.calls: list[list[Message]] = []

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        stream_handler=None,
    ) -> ModelTurn:
        self.calls.append(list(messages))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _CompactGuard:
    def __init__(self) -> None:
        self.compacted = 0
        self.compacted_messages = [
            Message.system("[compacted]"),
            Message.user("recent task"),
        ]

    async def compact_history(self, messages: list[Message]) -> list[Message]:
        self.compacted += 1
        return self.compacted_messages


async def test_resilience_runner_continues_after_truncated_answer() -> None:
    client = _ScriptedClient(
        [
            ModelTurn(text="partial", stop_reason="length"),
            ModelTurn(text="complete", stop_reason="stop"),
        ]
    )
    runner = ResilienceRunner(model_client=client, timeout_s=1.0)

    turn = await runner.run(
        system_prompt="system",
        messages=[Message.user("write answer")],
        tools=[],
    )

    assert turn.text == "partialcomplete"
    assert len(client.calls) == 2
    assert client.calls[1][-2] == Message.assistant("partial")
    assert "continue" in client.calls[1][-1].content.lower()


async def test_resilience_runner_compacts_history_after_overflow() -> None:
    guard = _CompactGuard()
    client = _ScriptedClient(
        [
            RuntimeError("maximum context length exceeded"),
            ModelTurn(text="after compact", stop_reason="stop"),
        ]
    )
    runner = ResilienceRunner(
        model_client=client,
        timeout_s=1.0,
        compact_fn=guard.compact_history,
    )

    turn = await runner.run(
        system_prompt="system",
        messages=[Message.user("old " + "x" * 100)],
        tools=[],
    )

    assert turn.text == "after compact"
    assert guard.compacted == 1
    assert client.calls[1] == guard.compacted_messages
