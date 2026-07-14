"""providers.openai_client — 基于 openai SDK 的原生 LLM 客户端。

``OpenAIModelClient`` 实现 ModelClient Protocol，直接使用 openai Python SDK。
model 字符串直接传给 OpenAI API，例如：
  - ``gpt-5.4-mini``
  - ``gpt-5.5``

支持流式和非流式两种模式，流式模式通过 stream_options=include_usage 获取 token 计数。
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Sequence
from typing import Any

from agent.debuglog import DebugLog, to_debug_json
from agent.models import Message, ModelTurn, TokenUsage, ToolCall
from agent.providers.base import StreamHandler


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    """把 SDK 返回的 tool arguments 规范化成 dict。"""
    if raw_arguments in (None, ""):
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        return json.loads(raw_arguments)
    raise TypeError(f"unsupported tool arguments type: {type(raw_arguments)!r}")


def _extract_usage(usage: Any) -> TokenUsage | None:
    """从 OpenAI usage 对象中抽取统一 TokenUsage。"""
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0
    if total == 0 and (prompt or completion):
        total = prompt + completion
    if total == 0:
        return None
    return TokenUsage(
        prompt_tokens=int(prompt),
        completion_tokens=int(completion),
        total_tokens=int(total),
    )


async def _maybe_await(value: Any) -> None:
    """支持同步或异步 stream_handler。"""
    if value is None:
        return
    if hasattr(value, "__await__"):
        await value


# extra_kwargs 中属于 AsyncOpenAI 构造函数的参数，不能传给 create()
_CLIENT_KWARGS = {"base_url", "organization", "timeout", "max_retries", "default_headers"}


class OpenAIModelClient:
    """基于 openai Python SDK 的 ModelClient 实现。

    当前请求形状是 Chat Completions 风格：messages + tools + 可选 stream。
    这个类的边界是 SDK 适配层：外部只看 ModelTurn，原始响应只写入 debug trace。
    """

    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0.0,
        api_key: str | None = None,
        base_url: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        debug_log: DebugLog | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = api_key
        self.base_url = base_url
        all_extra = extra_kwargs or {}
        # 分离 client 构造参数和 create() 请求参数
        self._client_extra = {k: v for k, v in all_extra.items() if k in _CLIENT_KWARGS}
        self.extra_kwargs = {k: v for k, v in all_extra.items() if k not in _CLIENT_KWARGS}
        self._log = debug_log or DebugLog.null()
        self._client: Any = None

    def _get_client(self) -> Any:
        """延迟创建 AsyncOpenAI，避免测试或 dry-run 时提前要求真实 API 配置。"""
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {**self._client_extra}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: StreamHandler | None = None,
    ) -> ModelTurn:
        """调用一次模型，并把响应规范化成 ModelTurn。

        非流式路径记录 model.response.raw；流式路径只记录聚合摘要，避免每个 chunk
        都写 debug log 造成日志膨胀。
        """
        client = self._get_client()
        payload_messages = self._build_messages(system_prompt, messages)

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": self.temperature,
            **self.extra_kwargs,
        }
        if tools:
            request_kwargs["tools"] = tools

        self._log.event(
            "model.request",
            {
                "stream": stream_handler is not None,
                "model": self.model,
                "message_count": len(payload_messages),
                "tool_count": len(tools),
            },
        )

        if stream_handler is not None:
            request_kwargs["stream"] = True
            request_kwargs["stream_options"] = {"include_usage": True}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                stream = await client.chat.completions.create(**request_kwargs)
                return await self._consume_stream(stream, stream_handler)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            response = await client.chat.completions.create(**request_kwargs)
        self._log.event("model.response.raw", {"response": to_debug_json(response)})

        choice = response.choices[0]
        tool_calls = self._normalize_tool_calls(choice.message.tool_calls)
        turn = ModelTurn(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "end_turn",
            usage=_extract_usage(response.usage),
        )
        self._log.event("model.response.normalized", {"turn": turn}, level="info")
        return turn

    def _build_messages(
        self,
        system_prompt: str,
        messages: Sequence[Message],
    ) -> list[dict[str, Any]]:
        """把内部 Message 列表转换为 OpenAI Chat Completions messages。

        ContextGuard 生成的 compaction system summary 会和主 system prompt 合并成第一条
        system 消息；tool 消息必须保留 tool_call_id，以满足 OpenAI 的配对要求。
        """
        payload: list[dict[str, Any]] = []

        compaction_summaries = [m.content for m in messages if m.role == "system" and m.content]
        combined_system = "\n\n---\n\n".join(filter(None, [system_prompt, *compaction_summaries]))
        if combined_system:
            payload.append({"role": "system", "content": combined_system})

        for message in messages:
            if message.role == "system":
                continue
            item: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.role == "assistant" and message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                item["tool_call_id"] = message.tool_call_id
                item["name"] = message.name
            payload.append(item)
        return payload

    @staticmethod
    def _normalize_tool_calls(raw_calls: Any) -> list[ToolCall]:
        """把非流式响应里的 SDK tool_calls 转成内部 ToolCall。"""
        if not raw_calls:
            return []
        normalized: list[ToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            name = raw_call.function.name
            if not name:
                raise ValueError("tool call missing function name")
            call_id = raw_call.id or f"tool-{index}"
            arguments = _parse_arguments(raw_call.function.arguments)
            normalized.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return normalized

    async def _consume_stream(
        self,
        stream: Any,
        stream_handler: StreamHandler,
    ) -> ModelTurn:
        """消费流式响应，累积文本、tool call 参数片段和 usage。

        OpenAI 流式 tool_call arguments 会分片返回，所以这里按 index 聚合 name/id/
        arguments，流结束后再解析 JSON。
        """
        text_parts: list[str] = []
        raw_tool_calls: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        finish_reasons: list[Any] = []
        last_usage: Any = None

        async for chunk in stream:
            chunk_count += 1
            if chunk.usage is not None:
                last_usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reasons.append(choice.finish_reason)
            delta = choice.delta
            if delta is None:
                continue

            text_delta = delta.content or ""
            if text_delta:
                text_parts.append(text_delta)
                await _maybe_await(stream_handler(text_delta))

            for raw_call in delta.tool_calls or []:
                index = raw_call.index if raw_call.index is not None else len(raw_tool_calls)
                entry = raw_tool_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if raw_call.id:
                    entry["id"] = raw_call.id
                if raw_call.function:
                    if raw_call.function.name:
                        entry["function"]["name"] = raw_call.function.name
                    if raw_call.function.arguments:
                        entry["function"]["arguments"] += raw_call.function.arguments

        usage = _extract_usage(last_usage)
        self._log.event(
            "model.stream.raw_summary",
            {
                "chunk_count": chunk_count,
                "text_length": sum(len(p) for p in text_parts),
                "tool_call_count": len(raw_tool_calls),
                "raw_tool_calls": [raw_tool_calls[i] for i in sorted(raw_tool_calls)],
                "finish_reasons": finish_reasons,
                "usage": usage,
            },
        )
        # 规范化累积的工具调用；跳过没有 name 或参数为空的条目。
        # 某些 API proxy 会发出只有 name、没有 arguments 的空 tool call 槽位。
        tool_calls: list[ToolCall] = []
        for idx in sorted(raw_tool_calls):
            entry = raw_tool_calls[idx]
            name = entry["function"]["name"]
            raw_args = entry["function"]["arguments"]
            if not name:
                continue
            if raw_args in (None, ""):
                continue
            parsed = _parse_arguments(raw_args)
            tool_calls.append(ToolCall(
                id=entry["id"] or f"tool-{idx}",
                name=name,
                arguments=parsed,
            ))

        turn = ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=finish_reasons[-1] if finish_reasons else "end_turn",
            usage=usage,
        )
        self._log.event("model.response.normalized", {"turn": turn}, level="info")
        return turn
