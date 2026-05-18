from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agent.debuglog import DebugLog
from agent.models import Message
from agent.providers.openai_client import OpenAIModelClient


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


class FakeChatCompletions:
    def __init__(self, handler):
        self.handler = handler

    async def create(self, **kwargs):
        return await self.handler(**kwargs)


class FakeAsyncOpenAI:
    handler = None
    init_kwargs: dict = {}

    def __init__(self, **kwargs):
        FakeAsyncOpenAI.init_kwargs = kwargs
        self.chat = SimpleNamespace(
            completions=FakeChatCompletions(FakeAsyncOpenAI.handler)
        )


def _install_fake_openai(monkeypatch, handler) -> None:
    FakeAsyncOpenAI.handler = handler
    FakeAsyncOpenAI.init_kwargs = {}
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI),
    )


@pytest.mark.asyncio
async def test_openai_client_parses_non_stream_response(monkeypatch) -> None:
    async def fake_create(**kwargs):
        assert kwargs["model"] == "fake-model"
        return SimpleNamespace(
            usage=None,
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
            ],
        )

    _install_fake_openai(monkeypatch, fake_create)

    client = OpenAIModelClient(model="fake-model")
    turn = await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
    )

    assert turn.text == "final answer"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "README.md"}


@pytest.mark.asyncio
async def test_openai_client_streams_text(monkeypatch) -> None:
    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        return FakeStream(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hello ", tool_calls=None),
                            finish_reason=None,
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="world", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                ),
            ]
        )

    _install_fake_openai(monkeypatch, fake_create)

    chunks: list[str] = []
    client = OpenAIModelClient(model="fake-model")
    turn = await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
        stream_handler=chunks.append,
    )

    assert turn.text == "hello world"
    assert chunks == ["hello ", "world"]


@pytest.mark.asyncio
async def test_openai_client_debug_log_aggregates_stream_chunks(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        return FakeStream(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="h", tool_calls=None),
                            finish_reason=None,
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=1,
                        completion_tokens=2,
                        total_tokens=3,
                    ),
                    choices=[],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="i", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                ),
            ]
        )

    _install_fake_openai(monkeypatch, fake_create)

    log_path = tmp_path / "debug.jsonl"
    client = OpenAIModelClient(
        model="fake-model",
        debug_log=DebugLog(log_path, session_id="stream-session"),
    )
    await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
        stream_handler=lambda _chunk: None,
    )

    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]

    assert [event["event"] for event in events] == [
        "model.request",
        "model.stream.raw_summary",
        "model.response.normalized",
    ]
    assert events[1]["payload"]["chunk_count"] == 3
    assert events[1]["payload"]["text_length"] == 2
    assert events[1]["payload"]["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_openai_client_debug_log_records_raw_response(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_create(**kwargs):
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="final answer", tool_calls=[]),
                )
            ],
        )

    _install_fake_openai(monkeypatch, fake_create)

    log_path = tmp_path / "debug.jsonl"
    client = OpenAIModelClient(
        model="fake-model",
        api_key="secret",
        debug_log=DebugLog(log_path, session_id="provider-session"),
    )
    await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
    )

    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]

    assert [event["event"] for event in events] == [
        "model.request",
        "model.response.raw",
        "model.response.normalized",
    ]
    assert FakeAsyncOpenAI.init_kwargs["api_key"] == "secret"
    assert events[1]["payload"]["response"]["choices"][0]["message"]["content"] == "final answer"
