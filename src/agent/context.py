from __future__ import annotations

from dataclasses import dataclass, field

from agent.models import Message


def _count_tokens(text: str, model: str) -> int:
    """Count tokens using litellm if available, else estimate from chars."""
    if model:
        try:
            from litellm import token_counter

            return token_counter(model=model, text=text)
        except Exception:
            pass
    return len(text) // 3 + 1


def _message_tokens(message: Message, model: str) -> int:
    text = message.content + "".join(str(call.arguments) for call in message.tool_calls)
    return _count_tokens(text, model)


def _total_tokens(messages: list[Message], model: str) -> int:
    return sum(_message_tokens(message, model) for message in messages)


# Legacy char-based helpers for tool result truncation (which operates on chars, not tokens).
def _message_size(message: Message) -> int:
    return len(message.content) + sum(len(str(call.arguments)) for call in message.tool_calls)


def _total_size(messages: list[Message]) -> int:
    return sum(_message_size(message) for message in messages)


def _format_for_summary(messages: list[Message], *, max_chars_per_entry: int = 300) -> str:
    lines: list[str] = []
    for message in messages:
        content = " ".join(message.content.split())
        if not content and message.tool_calls:
            names = ", ".join(call.name for call in message.tool_calls)
            content = f"tool calls: {names}"
        if not content:
            continue
        lines.append(f"- {message.role}: {content[:max_chars_per_entry]}")
    return "\n".join(lines)


SUMMARY_PROMPT = (
    "Compress the following conversation into a concise summary. "
    "Preserve: file paths, key decisions, errors encountered, pending tasks, "
    "and any important context the assistant will need to continue the conversation. "
    "Do NOT include pleasantries or filler. Be factual and dense.\n\n"
)


@dataclass(frozen=True)
class ContextGuard:
    max_tokens: int = 12000
    keep_recent: int = 6
    tool_result_max_chars: int = 6000
    summary_entry_chars: int = 240
    compact_model: str = ""
    model: str = ""
    _compact_model_kwargs: dict = field(default_factory=dict)

    async def prepare(self, messages: list[Message]) -> list[Message]:
        current = self.truncate_large_tool_results(messages)
        if _total_tokens(current, self.model) <= self.max_tokens:
            return current
        return await self.compact_history(current)

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

    async def compact_history(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self.keep_recent:
            return self._trim_recent(messages)

        split = self._safe_split_index(messages, len(messages) - self.keep_recent)
        older = messages[:split]
        recent = messages[split:]
        summary = await self._summarize(older)
        compacted = [Message.system(summary), *recent]
        return self._trim_recent(compacted)

    def _safe_split_index(self, messages: list[Message], target: int) -> int:
        """Find a split point that doesn't orphan tool messages from their assistant."""
        split = max(0, min(target, len(messages)))
        while split < len(messages) and messages[split].role == "tool":
            split += 1
        if split >= len(messages):
            split = target
        return split

    async def _summarize(self, messages: list[Message]) -> str:
        formatted = _format_for_summary(messages)
        if not formatted:
            return "Earlier conversation was compacted (no content to summarize)."

        # Use compact_model if set, otherwise fall back to the main model.
        effective_model = self.compact_model or self.model
        if effective_model:
            llm_summary = await self._llm_summarize(formatted, model=effective_model)
            if llm_summary:
                return (
                    "[Conversation summary — source files and tool outputs remain the source of truth]\n\n"
                    + llm_summary
                )

        return self._mechanical_summary(messages)

    async def _llm_summarize(self, formatted: str, *, model: str) -> str:
        try:
            from litellm import acompletion

            response = await acompletion(
                model=model,
                messages=[{"role": "user", "content": SUMMARY_PROMPT + formatted}],
                temperature=0.0,
                max_tokens=1024,
                stream=False,
                **self._compact_model_kwargs,
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception:
            return ""

    def _mechanical_summary(self, messages: list[Message]) -> str:
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
        if _total_tokens(messages, self.model) <= self.max_tokens:
            return messages

        char_budget = self.max_tokens * 4
        budget_per_message = max(500, char_budget // max(1, len(messages)))
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
