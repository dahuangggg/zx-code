"""providers.litellm_client — 基于 litellm 的统一 LLM 客户端。

``LiteLLMModelClient`` 实现 ModelClient Protocol，通过 litellm 支持 100+ 模型。
litellm 的 model 字符串格式：``<provider>/<model_name>``，例如：
  - ``openai/gpt-4o-mini``
  - ``anthropic/claude-sonnet-4-6``
  - ``ollama/llama3``

流式处理：
  - stream=True 时，逐 chunk 调用 stream_handler 实现实时输出
  - 同时收集完整文本和 tool_calls，最终组装为 ModelTurn

工具调用收集：
  - 跨多个 chunk 累积 tool_calls（litellm 流式返回时分片）
  - arguments 字段为字符串，最终 JSON 解析
"""

from __future__ import annotations


import json
from collections.abc import Sequence
from typing import Any

from agent.debuglog import DebugLog, to_debug_json
from agent.models import Message, ModelTurn, ToolCall
from agent.providers.base import StreamHandler


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            item_type = _read_attr(item, "type")
            if item_type == "text":
                parts.append(_read_attr(item, "text", ""))
                continue
            text = _read_attr(item, "text")
            if text:
                parts.append(str(text))
        return "".join(parts)
    return str(content)


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if raw_arguments in (None, ""):
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        return json.loads(raw_arguments)
    raise TypeError(f"unsupported tool arguments type: {type(raw_arguments)!r}")


def _normalize_tool_calls(raw_calls: Any) -> list[ToolCall]:
    normalized: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls or []):
        function = _read_attr(raw_call, "function", {})
        name = _read_attr(function, "name") or _read_attr(raw_call, "name")
        if not name:
            raise ValueError("tool call missing function name")
        call_id = _read_attr(raw_call, "id") or f"tool-{index}"
        arguments = _parse_arguments(_read_attr(function, "arguments", "{}"))
        normalized.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
            )
        )
    return normalized


async def _maybe_await(value: Any) -> None:
    if value is None:
        return
    if hasattr(value, "__await__"):
        await value


class LiteLLMModelClient:
    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0.0,
        extra_kwargs: dict[str, Any] | None = None,
        debug_log: DebugLog | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.extra_kwargs = extra_kwargs or {}
        self.debug_log = debug_log

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: StreamHandler | None = None,
    ) -> ModelTurn:
        try:
            import litellm
            from litellm import acompletion

            litellm.drop_params = True
        except ImportError as exc:
            raise RuntimeError(
                "litellm is not installed. Run `uv add litellm typer rich pydantic` first."
            ) from exc

        payload_messages = self._build_messages(system_prompt, messages)
        request_kwargs = {
            "model": self.model,
            "messages": payload_messages,
            "tools": tools,
            "temperature": self.temperature,
            **self.extra_kwargs,
        }
        if self.debug_log is not None:
            self.debug_log.event(
                "model.request",
                {
                    "stream": stream_handler is not None,
                    "kwargs": _redact_request_kwargs(request_kwargs),
                },
            )

        if stream_handler is not None:
            stream = await acompletion(stream=True, **request_kwargs)
            return await self._consume_stream(stream, stream_handler)

        response = await acompletion(stream=False, **request_kwargs)
        if self.debug_log is not None:
            self.debug_log.event("model.response.raw", {"response": to_debug_json(response)})
        choice = response.choices[0]
        message = _read_attr(choice, "message", {})
        turn = ModelTurn(
            text=_extract_text(_read_attr(message, "content")),
            tool_calls=_normalize_tool_calls(_read_attr(message, "tool_calls", [])),
            stop_reason=_read_attr(choice, "finish_reason", "end_turn"),
        )
        if self.debug_log is not None:
            self.debug_log.event("model.response.normalized", {"turn": turn})
        return turn

    def _build_messages(
        self,
        system_prompt: str,
        messages: Sequence[Message],
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []

        # 将消息列表中的 system 消息（来自上下文压缩摘要）合并到系统提示里，
        # 避免向模型发送多条 system 消息（部分模型不支持）
        compaction_summaries = [m.content for m in messages if m.role == "system" and m.content]
        combined_system = "\n\n---\n\n".join(filter(None, [system_prompt, *compaction_summaries]))
        if combined_system:
            payload.append({"role": "system", "content": combined_system})

        for message in messages:
            if message.role == "system":
                continue  # 已合并到 combined_system，跳过
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

    async def _consume_stream(
        self,
        stream: Any,
        stream_handler: StreamHandler,
    ) -> ModelTurn:
        text_parts: list[str] = []
        raw_tool_calls: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        finish_reasons: list[Any] = []

        async for chunk in stream:
            chunk_count += 1
            choice = chunk.choices[0]
            finish_reason = _read_attr(choice, "finish_reason")
            if finish_reason:
                finish_reasons.append(finish_reason)
            delta = _read_attr(choice, "delta", {})

            text_delta = _extract_text(_read_attr(delta, "content"))
            if text_delta:
                text_parts.append(text_delta)
                await _maybe_await(stream_handler(text_delta))

            for raw_call in _read_attr(delta, "tool_calls", []) or []:
                index = _read_attr(raw_call, "index", len(raw_tool_calls))
                entry = raw_tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {
                            "name": "",
                            "arguments": "",
                        },
                    },
                )
                call_id = _read_attr(raw_call, "id")
                if call_id:
                    entry["id"] = call_id
                function = _read_attr(raw_call, "function", {})
                name = _read_attr(function, "name")
                if name:
                    entry["function"]["name"] = name
                arguments = _read_attr(function, "arguments")
                if arguments:
                    entry["function"]["arguments"] += arguments

        if self.debug_log is not None:
            self.debug_log.event(
                "model.stream.raw_summary",
                {
                    "chunk_count": chunk_count,
                    "text": "".join(text_parts),
                    "raw_tool_calls": [
                        raw_tool_calls[index] for index in sorted(raw_tool_calls)
                    ],
                    "finish_reasons": finish_reasons,
                },
            )
        turn = ModelTurn(
            text="".join(text_parts),
            tool_calls=_normalize_tool_calls(
                [raw_tool_calls[index] for index in sorted(raw_tool_calls)]
            ),
            stop_reason=finish_reasons[-1] if finish_reasons else "end_turn",
        )
        if self.debug_log is not None:
            self.debug_log.event("model.response.normalized", {"turn": turn})
        return turn


def _redact_request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(kwargs)
    for key in list(redacted):
        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            redacted[key] = "[redacted]"
    return redacted
