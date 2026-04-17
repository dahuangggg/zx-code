from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from agent.models import Message
from agent.providers.litellm_client import LiteLLMModelClient


class FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


@pytest.mark.asyncio
async def test_litellm_client_parses_non_stream_response(monkeypatch) -> None:
    async def fake_acompletion(**kwargs):
        assert kwargs["model"] == "fake-model"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="final answer",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="read_file",
                                    arguments='{"path":"README.md"}',
                                ),
                            )
                        ],
                    ),
                )
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(acompletion=fake_acompletion),
    )

    client = LiteLLMModelClient(model="fake-model")
    turn = await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
    )

    assert turn.text == "final answer"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "README.md"}


@pytest.mark.asyncio
async def test_litellm_client_streams_text(monkeypatch) -> None:
    async def fake_acompletion(**kwargs):
        assert kwargs["stream"] is True
        return FakeStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hello ", tool_calls=None)
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="world", tool_calls=None)
                        )
                    ]
                ),
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(acompletion=fake_acompletion),
    )

    chunks: list[str] = []
    client = LiteLLMModelClient(model="fake-model")
    turn = await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
        stream_handler=chunks.append,
    )

    assert turn.text == "hello world"
    assert chunks == ["hello ", "world"]
