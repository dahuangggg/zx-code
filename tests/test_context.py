from __future__ import annotations

import asyncio

from agent.core.context import ContextGuard
from agent.models import Message, ToolCall


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_context_guard_truncates_large_tool_results() -> None:
    guard = ContextGuard(max_tokens=250, tool_result_max_chars=10)
    messages = [Message.tool("call-1", "read_file", "x" * 40)]

    prepared = _run(guard.prepare(messages))

    assert "tool result truncated" in prepared[0].content
    assert len(prepared[0].content) < 80


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
    guard = ContextGuard(max_tokens=20, keep_recent=2)
    messages = [
        Message.user("old " + "x" * 100),
        Message.tool("call-1", "read_file", "orphaned result"),
        Message.user("recent"),
    ]

    prepared = _run(guard.prepare(messages))

    # The safe split should push the orphan tool into the older group (summarized),
    # so only system summary + recent user remain.
    roles = [message.role for message in prepared]
    assert "tool" not in roles or roles.count("tool") == 0
    assert prepared[-1].content == "recent"


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


def test_context_guard_llm_summarize_fallback() -> None:
    """When compact_model is set but LLM fails, falls back to mechanical summary."""
    guard = ContextGuard(
        max_tokens=30,
        keep_recent=2,
        summary_entry_chars=20,
        compact_model="nonexistent/model-that-will-fail",
    )
    messages = [Message.user(f"old message {index} " + "x" * 30) for index in range(5)]
    messages.append(Message.user("recent one"))
    messages.append(Message.assistant("recent two"))

    prepared = _run(guard.prepare(messages))

    assert prepared[0].role == "system"
    assert "compacted" in prepared[0].content.lower()
    assert prepared[-1].content == "recent two"
