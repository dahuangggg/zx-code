from __future__ import annotations

import asyncio

from agent.core.context import ContextGuard
from agent.models import Message, ToolCall


def _run(coro):
    return asyncio.run(coro)


def test_context_guard_preserves_tool_results_for_unified_truncator() -> None:
    guard = ContextGuard(max_tokens=250)
    messages = [Message.tool("call-1", "read_file", "x" * 40)]

    prepared = _run(guard.prepare(messages))

    assert prepared[0].content == "x" * 40


def test_context_guard_compacts_old_history() -> None:
    guard = ContextGuard(max_tokens=30, keep_recent=2, summary_entry_chars=20)
    messages = [Message.user(f"old message {index} " + "x" * 30) for index in range(5)]
    messages.append(Message.user("recent one"))
    messages.append(Message.assistant("recent two"))

    prepared = _run(guard.prepare(messages))

    assert prepared[0].role == "system"
    assert "compacted" in prepared[0].content.lower() or "summary" in prepared[0].content.lower()
    assert prepared[-2].content == "recent one"
    assert prepared[-1].content == "recent two"


def test_context_guard_safe_split_skips_orphan_tool_messages() -> None:
    guard = ContextGuard(max_tokens=10, keep_recent=2)
    messages = [
        Message.user("old " + "x" * 200),
        Message.tool("call-1", "read_file", "orphaned result " + "y" * 100),
        Message.user("recent"),
    ]

    prepared = _run(guard.prepare(messages))

    # The safe split should push the orphan tool into the older group (summarized),
    # so only system summary + recent user remain.
    roles = [message.role for message in prepared]
    assert "tool" not in roles or roles.count("tool") == 0
    assert prepared[-1].content == "recent"


def test_context_guard_does_not_keep_orphan_tool_inside_recent_window() -> None:
    guard = ContextGuard(max_tokens=15, keep_recent=3)
    messages = [
        Message.user("old " + "x" * 200),
        Message.user("recent question"),
        Message.tool("call-1", "read_file", "orphaned result " + "y" * 100),
        Message.assistant("recent answer"),
    ]

    prepared = _run(guard.prepare(messages))

    assert [message.role for message in prepared] == ["system", "user", "assistant"]
    assert prepared[-2].content == "recent question"
    assert prepared[-1].content == "recent answer"


def test_context_guard_keeps_tool_message_with_recent_assistant_call() -> None:
    guard = ContextGuard(max_tokens=20, keep_recent=2)
    messages = [
        Message.user("old " + "x" * 100),
        Message.assistant(
            "using tool",
            tool_calls=[
                ToolCall(id="call-1", name="read_file", arguments={"path": "x"})
            ],
        ),
        Message.tool("call-1", "read_file", "result"),
    ]

    prepared = _run(guard.prepare(messages))

    assert [message.role for message in prepared] == ["system", "assistant", "tool"]


def test_context_guard_uses_mechanical_summary_without_summary_client() -> None:
    """Without an injected summary client, compaction stays local and deterministic."""
    guard = ContextGuard(
        max_tokens=30,
        keep_recent=2,
        summary_entry_chars=20,
    )
    messages = [Message.user(f"old message {index} " + "x" * 30) for index in range(5)]
    messages.append(Message.user("recent one"))
    messages.append(Message.assistant("recent two"))

    prepared = _run(guard.prepare(messages))

    assert prepared[0].role == "system"
    assert "compacted" in prepared[0].content.lower()
    assert prepared[-1].content == "recent two"


def test_context_guard_uses_injected_summary_client() -> None:
    class FakeSummaryClient:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        async def summarize(self, formatted: str) -> str:
            self.inputs.append(formatted)
            return "summary from injected client"

    summary_client = FakeSummaryClient()
    guard = ContextGuard(
        max_tokens=30,
        keep_recent=2,
        summary_client=summary_client,
    )
    messages = [Message.user(f"old message {index} " + "x" * 30) for index in range(4)]
    messages.append(Message.user("recent one"))
    messages.append(Message.assistant("recent two"))

    prepared = _run(guard.prepare(messages))

    assert prepared[0].role == "system"
    assert "summary from injected client" in prepared[0].content
    assert "old message 0" in summary_client.inputs[0]
    assert prepared[-2].content == "recent one"
    assert prepared[-1].content == "recent two"
