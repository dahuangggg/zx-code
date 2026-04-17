from __future__ import annotations

from dataclasses import dataclass

from agent.models import Message


def _message_size(message: Message) -> int:
    return len(message.content) + sum(len(str(call.arguments)) for call in message.tool_calls)


def _total_size(messages: list[Message]) -> int:
    return sum(_message_size(message) for message in messages)


@dataclass(frozen=True)
class ContextGuard:
    max_chars: int = 40000
    keep_recent: int = 24
    tool_result_max_chars: int = 6000
    summary_entry_chars: int = 240

    def prepare(self, messages: list[Message]) -> list[Message]:
        current = self.truncate_large_tool_results(messages)
        if _total_size(current) <= self.max_chars:
            return current
        return self.compact_history(current)

    def truncate_large_tool_results(self, messages: list[Message]) -> list[Message]:
        truncated: list[Message] = []
        for message in messages:
            if (
                message.role == "tool"
                and self.tool_result_max_chars > 0
                and len(message.content) > self.tool_result_max_chars
            ):
                omitted = len(message.content) - self.tool_result_max_chars
                content = (
                    message.content[: self.tool_result_max_chars]
                    + f"\n\n[tool result truncated, omitted {omitted} chars]"
                )
                truncated.append(message.model_copy(update={"content": content}))
            else:
                truncated.append(message)
        return truncated

    def compact_history(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self.keep_recent:
            return self._trim_recent(messages)

        older = messages[: -self.keep_recent]
        recent = messages[-self.keep_recent :]
        summary = self._summarize(older)
        compacted = [Message.system(summary), *self._drop_orphan_tool_messages(recent)]
        return self._trim_recent(compacted)

    def _summarize(self, messages: list[Message]) -> str:
        lines = [
            "Earlier conversation was compacted to stay within the context budget.",
            "Use this as continuity only; source files and tool outputs remain the source of truth.",
        ]
        for message in messages:
            content = " ".join(message.content.split())
            if not content and message.tool_calls:
                names = ", ".join(call.name for call in message.tool_calls)
                content = f"tool calls: {names}"
            if not content:
                continue
            lines.append(f"- {message.role}: {content[: self.summary_entry_chars]}")
        return "\n".join(lines)

    def _trim_recent(self, messages: list[Message]) -> list[Message]:
        if _total_size(messages) <= self.max_chars:
            return messages

        budget_per_message = max(500, self.max_chars // max(1, len(messages)))
        trimmed: list[Message] = []
        for message in messages:
            if len(message.content) <= budget_per_message:
                trimmed.append(message)
                continue
            omitted = len(message.content) - budget_per_message
            content = (
                message.content[:budget_per_message]
                + f"\n\n[message truncated, omitted {omitted} chars]"
            )
            trimmed.append(message.model_copy(update={"content": content}))
        return trimmed

    def _drop_orphan_tool_messages(self, messages: list[Message]) -> list[Message]:
        normalized: list[Message] = []
        tool_context_open = False

        for message in messages:
            if message.role == "assistant":
                normalized.append(message)
                tool_context_open = bool(message.tool_calls)
                continue
            if message.role == "tool":
                if tool_context_open:
                    normalized.append(message)
                continue

            normalized.append(message)
            tool_context_open = False

        return normalized
