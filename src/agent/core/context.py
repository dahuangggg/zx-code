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

import logging
from dataclasses import dataclass, field

from agent.models import Message

logger = logging.getLogger(__name__)


def _count_tokens(text: str, model: str) -> int:
    """Count tokens using litellm if available, else estimate from chars."""
    if model:
        try:
            from litellm import token_counter

            return token_counter(model=model, text=text)
        except Exception:
            pass
    # 兜底估算：经验值 ~3 字符/token（英文偏低、中文偏高），+1 防止空串返回 0
    return len(text) // 3 + 1


def _message_tokens(message: Message, model: str) -> int:
    """估算单条消息的 token 数（content + tool_calls 参数合计）。"""
    # 把 tool_calls 的参数也算进去，否则带大量工具调用的 assistant 消息会被严重低估
    text = message.content + "".join(str(call.arguments) for call in message.tool_calls)
    return _count_tokens(text, model)


def _total_tokens(messages: list[Message], model: str) -> int:
    """估算整段对话历史的总 token 数，用于判断是否超出 max_tokens 预算。"""
    return sum(_message_tokens(message, model) for message in messages)


# Legacy char-based helpers for tool result truncation (which operates on chars, not tokens).
def _message_size(message: Message) -> int:
    """单条消息按字符计算长度，供按字符截断的逻辑使用（与 token 计数解耦）。"""
    return len(message.content) + sum(len(str(call.arguments)) for call in message.tool_calls)


def _total_size(messages: list[Message]) -> int:
    """整段历史按字符计算总长度。"""
    return sum(_message_size(message) for message in messages)


def _format_for_summary(messages: list[Message], *, max_chars_per_entry: int = 300) -> str:
    """将消息列表序列化成喂给摘要 LLM 的纯文本格式，逐条压缩空白并限制单条长度。"""
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
    # 工具调用协议要求 assistant(tool_calls) 后必须跟齐对应 tool 结果，
    # 切窗口时不能把这对消息切开，否则模型会因协议不完整报错
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
            # 仅当所有期望的 tool 结果都齐了，才标记这一组"可原样保留"
            groups.append((group, seen_ids == expected_ids))
            continue

        # 单条 tool 消息无对应 assistant tool_calls → 视为孤儿，只能进摘要
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
        """主入口：在每次 LLM 调用前对消息列表做预算控制，返回可直接送模型的副本。"""
        # 两步式预算控制：先做廉价的单条截断，仍超预算才走昂贵的 LLM 摘要
        current = self.truncate_large_tool_results(messages)
        if _total_tokens(current, self.model) <= self.max_tokens:
            return current
        return await self.compact_history(current)

    def truncate_large_tool_results(self, messages: list[Message]) -> list[Message]:
        """对单条超长的 tool 消息做硬截断，附上省略字节数提示；不改变消息总数。"""
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
        """把"远端旧消息"摘要为一条 system 消息 + "近端消息"原样保留，整体替换历史。"""
        if len(messages) <= self.keep_recent:
            return self._trim_recent(messages)

        older, recent = self._split_for_compaction(messages)
        summary = await self._summarize(older)
        compacted = [Message.system(summary), *recent]
        return self._trim_recent(compacted)

    def _split_for_compaction(self, messages: list[Message]) -> tuple[list[Message], list[Message]]:
        """按工具调用协议分组后，从后往前累计到 keep_recent，切出 (older, recent) 两段。"""
        groups = _message_groups(messages)
        split = len(groups)
        recent_count = 0

        # 从后往前按组累计，直到达到 keep_recent 条；按组而非按条切，保证不破坏工具协议
        while split > 0 and recent_count < self.keep_recent:
            split -= 1
            group, _ = groups[split]
            recent_count += len(group)

        older = [
            message
            for group, _ in groups[:split]
            for message in group
        ]
        # 近窗内的"不安全组"（如孤儿 tool 消息）也降级进摘要，不能直接喂给模型
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
        """生成历史摘要：优先调用 LLM，失败则降级到机械摘要，保证总能拿到一个字符串。"""
        formatted = _format_for_summary(messages)
        if not formatted:
            return "Earlier conversation was compacted (no content to summarize)."

        # Use compact_model if set, otherwise fall back to the main model.
        effective_model = self.compact_model or self.model
        if effective_model:
            llm_summary = await self._llm_summarize(formatted, model=effective_model)
            if llm_summary:
                # 前缀提醒模型：摘要是有损的，真实事实以文件/工具输出为准，避免模型把摘要当成绝对真相
                return (
                    "[Conversation summary — source files and tool outputs remain the source of truth]\n\n"
                    + llm_summary
                )

        # LLM 调用失败或未配置模型时降级为机械摘要，保证 compact 永远能给出结果
        return self._mechanical_summary(messages)

    async def _llm_summarize(self, formatted: str, *, model: str) -> str:
        """用廉价模型（如 gpt-4o-mini）生成摘要；任何异常都记录后返回空串，让上层走降级。"""
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
        except Exception as exc:
            logger.warning("LLM summarization failed (model=%s), falling back to mechanical summary: %s", model, exc)
            return ""

    def _mechanical_summary(self, messages: list[Message]) -> str:
        """不依赖 LLM 的兜底摘要：把每条消息压成一行短文本拼起来，永远成功。"""
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
        """摘要后仍超预算时的最后兜底：把每条消息按字符均摊上限硬截，确保不再超 max_tokens。"""
        # 兜底：摘要完仍可能超预算（如近窗里有巨型 tool 输出），按字符均摊每条上限做硬截断
        if _total_tokens(messages, self.model) <= self.max_tokens:
            return messages

        # 经验比例 ~4 字符/token，与 _count_tokens 的 //3 不一致是有意的：
        # 这里宁可宽松一点也别截得过狠，让有用内容尽量保留
        char_budget = self.max_tokens * 4
        # 每条至少留 500 字，避免极端情况下消息被切到只剩几个字毫无用处
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
