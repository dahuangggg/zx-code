from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.loop import run_task
from agent.models import AgentConfig, Message, ModelTurn
from agent.sessions import SessionStore, safe_session_id
from agent.tools import build_default_registry


class InspectingModelClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.seen_messages: list[list[Message]] = []

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        stream_handler=None,
    ) -> ModelTurn:
        self.seen_messages.append(list(messages))
        return ModelTurn(text=self.text)


def test_session_store_appends_and_rebuilds_messages(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.append_message("main/session", Message.user("hello"))
    store.append_message("main/session", Message.assistant("hi"))

    rebuilt = store.rebuild_messages("main/session")

    assert [message.role for message in rebuilt] == ["user", "assistant"]
    assert [message.content for message in rebuilt] == ["hello", "hi"]
    assert store.path_for("main/session").name == "main_session.jsonl"
    assert safe_session_id("../bad/session") == "bad_session"


@pytest.mark.asyncio
async def test_run_task_rebuilds_previous_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    registry = build_default_registry()

    first = InspectingModelClient("first answer")
    await run_task(
        "first task",
        model_client=first,
        tool_registry=registry,
        config=AgentConfig(session_id="demo", stream=False),
        session_store=store,
    )

    second = InspectingModelClient("second answer")
    result = await run_task(
        "second task",
        model_client=second,
        tool_registry=registry,
        config=AgentConfig(session_id="demo", stream=False),
        session_store=store,
    )

    seen_contents = [message.content for message in second.seen_messages[0]]
    assert seen_contents == ["first task", "first answer", "second task"]
    assert result.session_id == "demo"

