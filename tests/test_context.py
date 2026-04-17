from __future__ import annotations

from agent.context import ContextGuard
from agent.models import Message, ToolCall


def test_context_guard_truncates_large_tool_results() -> None:
    guard = ContextGuard(max_chars=1000, tool_result_max_chars=10)
    messages = [Message.tool("call-1", "read_file", "x" * 40)]

    prepared = guard.prepare(messages)

    assert "tool result truncated" in prepared[0].content
    assert len(prepared[0].content) < 80


def test_context_guard_compacts_old_history() -> None:
    guard = ContextGuard(max_chars=120, keep_recent=2, summary_entry_chars=20)
    messages = [Message.user(f"old message {index} " + "x" * 30) for index in range(5)]
    messages.append(Message.user("recent one"))
    messages.append(Message.assistant("recent two"))

    prepared = guard.prepare(messages)

    assert prepared[0].role == "system"
    assert "compacted" in prepared[0].content
    assert prepared[-2].content == "recent one"
    assert prepared[-1].content == "recent two"


def test_context_guard_drops_orphan_tool_messages_after_compact() -> None:
    guard = ContextGuard(max_chars=80, keep_recent=2)
    messages = [
        Message.user("old " + "x" * 100),
        Message.tool("call-1", "read_file", "orphaned result"),
        Message.user("recent"),
    ]

    prepared = guard.prepare(messages)

    assert [message.role for message in prepared] == ["system", "user"]


def test_context_guard_keeps_tool_message_with_recent_assistant_call() -> None:
    guard = ContextGuard(max_chars=80, keep_recent=2)
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

    prepared = guard.prepare(messages)

    assert [message.role for message in prepared] == ["system", "assistant", "tool"]
