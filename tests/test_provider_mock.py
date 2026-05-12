from __future__ import annotations

import sys
import json
from types import SimpleNamespace

import pytest

from agent.models import Message
from agent.debuglog import DebugLog
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


@pytest.mark.asyncio
async def test_litellm_client_debug_log_aggregates_stream_chunks(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_acompletion(**kwargs):
        assert kwargs["stream"] is True
        return FakeStream(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="h", tool_calls=None),
                            finish_reason=None,
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="i", tool_calls=None),
                            finish_reason="stop",
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

    log_path = tmp_path / "debug.jsonl"
    client = LiteLLMModelClient(
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
    assert events[1]["payload"]["chunk_count"] == 2
    assert events[1]["payload"]["text"] == "hi"


@pytest.mark.asyncio
async def test_litellm_client_debug_log_records_raw_response(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="final answer", tool_calls=[]),
                )
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(acompletion=fake_acompletion),
    )

    log_path = tmp_path / "debug.jsonl"
    client = LiteLLMModelClient(
        model="fake-model",
        extra_kwargs={"api_key": "secret"},
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
    assert events[0]["payload"]["kwargs"]["api_key"] == "[redacted]"
    assert events[1]["payload"]["response"]["choices"][0]["message"]["content"] == "final answer"
