"""core.context — 上下文窗口管理与历史压缩（s06）。

ContextGuard 在每次 LLM 调用前被调用（``prepare()``），执行两个任务：

1. **截断超大工具结果**
   单条工具输出超过 ``tool_result_max_chars`` 时截断，附上省略字节数提示。
   这是轻量级保护，不影响历史结构。

2. **历史压缩（Compact）**
   总 token 超过 ``max_tokens`` 时触发：
   - 保留最近 ``keep_recent`` 条消息的原文（近场保留）
   - 对更早的消息生成摘要（远程压缩），策略优先级：
       a. LLM 摘要（``compact_model`` 指定的廉价模型，如 gpt-4o-mini）
       b. 机械摘要兜底（LLM 调用失败时降级）
   - 摘要作为一条 system 消息插入，替换旧历史

Token 计数使用 ``litellm.token_counter()``（自动选择正确 tokenizer），
不可用时降级为 ``len(text) // 3 + 1`` 的估算。
"""
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


def _message_groups(messages: list[Message]) -> list[tuple[list[Message], bool]]:
    """Group messages along tool-call protocol boundaries.

    The boolean marks whether the group is safe to keep verbatim in the recent
    window. Orphan tool messages and incomplete assistant tool-call groups are
    summarized instead of sent back to the model as invalid protocol messages.
    """
    groups: list[tuple[list[Message], bool]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "assistant" and message.tool_calls:
            expected_ids = {call.id for call in message.tool_calls}
            seen_ids: set[str] = set()
            group = [message]
            index += 1
            while index < len(messages) and messages[index].role == "tool":
                tool_message = messages[index]
                if tool_message.tool_call_id not in expected_ids:
                    break
                seen_ids.add(tool_message.tool_call_id or "")
                group.append(tool_message)
                index += 1
            groups.append((group, seen_ids == expected_ids))
            continue

        groups.append(([message], message.role != "tool"))
        index += 1
    return groups


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

        older, recent = self._split_for_compaction(messages)
        summary = await self._summarize(older)
        compacted = [Message.system(summary), *recent]
        return self._trim_recent(compacted)

    def _split_for_compaction(self, messages: list[Message]) -> tuple[list[Message], list[Message]]:
        groups = _message_groups(messages)
        split = len(groups)
        recent_count = 0

        while split > 0 and recent_count < self.keep_recent:
            split -= 1
            group, _ = groups[split]
            recent_count += len(group)

        older = [
            message
            for group, _ in groups[:split]
            for message in group
        ]
        older.extend(
            message
            for group, safe_to_keep in groups[split:]
            if not safe_to_keep
            for message in group
        )
        recent = [
            message
            for group, safe_to_keep in groups[split:]
            if safe_to_keep
            for message in group
        ]
        return older, recent

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
