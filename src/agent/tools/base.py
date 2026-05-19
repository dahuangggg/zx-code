"""tools.base — abstract base class for all tools."""
from __future__ import annotations

import time as _time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from agent.models import ToolEnvelope, ToolError, ToolResult


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_PARAM = "INVALID_PARAM"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    IS_DIRECTORY = "IS_DIRECTORY"
    BINARY_FILE = "BINARY_FILE"
    CONFLICT = "CONFLICT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


def _build_envelope(
    status: str,
    *,
    data: dict[str, Any],
    text: str,
    extra_context: dict[str, Any] | None,
    stats: dict[str, Any] | None,
) -> ToolEnvelope:
    context: dict[str, Any] = {"cwd": "."}
    if extra_context:
        context.update(extra_context)
    return ToolEnvelope(
        status=status,
        data=data,
        text=text,
        stats=stats or {},
        context=context,
    )


def _build_error_tool_envelope(
    code: ErrorCode,
    message: str,
    *,
    stats: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> ToolEnvelope:
    return ToolEnvelope(
        status="error",
        data={},
        text=message,
        error=ToolError(code=code.value, message=message),
        stats=stats or {"time_ms": 0},
        context={"cwd": "."} | (context or {}),
    )


def build_error_envelope(code: ErrorCode, message: str) -> str:
    """Standalone helper for non-Tool contexts (registry, circuit breaker)."""
    return _build_error_tool_envelope(code, message).model_dump_json(exclude_none=True)


def _normalize_legacy_result(raw: str | dict[str, Any], *, call_id: str, name: str) -> ToolEnvelope:
    legacy_content: str | dict[str, Any]
    legacy_content = raw
    return ToolResult(
        call_id=call_id,
        name=name,
        content=legacy_content,  # type: ignore[call-arg]
    ).envelope


def _inject_meta(
    envelope: ToolEnvelope,
    *,
    time_ms: int,
    params_input: dict[str, Any],
) -> ToolEnvelope:
    stats = dict(envelope.stats)
    stats["time_ms"] = time_ms
    context = {"cwd": "."} | dict(envelope.context)
    context["params_input"] = params_input
    return envelope.model_copy(update={"stats": stats, "context": context})


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def success(
        self,
        *,
        data: dict[str, Any],
        text: str,
        extra_context: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> ToolEnvelope:
        return _build_envelope("success", data=data, text=text, extra_context=extra_context, stats=stats)

    def partial(
        self,
        *,
        data: dict[str, Any],
        text: str,
        extra_context: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> ToolEnvelope:
        return _build_envelope("partial", data=data, text=text, extra_context=extra_context, stats=stats)

    def error(self, *, code: ErrorCode, message: str) -> ToolEnvelope:
        return _build_error_tool_envelope(code, message)

    @abstractmethod
    async def run(self, arguments: BaseModel) -> ToolEnvelope:
        raise NotImplementedError

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        t0 = _time.monotonic()
        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            elapsed_ms = round((_time.monotonic() - t0) * 1000)
            envelope = _inject_meta(
                _build_error_tool_envelope(
                    ErrorCode.INVALID_PARAM,
                    f"invalid arguments for {self.name}: {exc.errors(include_url=False)}",
                ),
                time_ms=elapsed_ms,
                params_input=arguments,
            )
            return ToolResult(call_id=call_id, name=self.name, envelope=envelope)
        try:
            raw = await self.run(parsed)
            if isinstance(raw, ToolEnvelope):
                envelope = raw
            else:
                envelope = _normalize_legacy_result(raw, call_id=call_id, name=self.name)
        except Exception as exc:
            elapsed_ms = round((_time.monotonic() - t0) * 1000)
            envelope = _inject_meta(
                _build_error_tool_envelope(
                    ErrorCode.INTERNAL_ERROR,
                    f"{type(exc).__name__}: {exc}",
                ),
                time_ms=elapsed_ms,
                params_input=arguments,
            )
            return ToolResult(call_id=call_id, name=self.name, envelope=envelope)
        elapsed_ms = round((_time.monotonic() - t0) * 1000)
        envelope = _inject_meta(envelope, time_ms=elapsed_ms, params_input=arguments)
        return ToolResult(
            call_id=call_id,
            name=self.name,
            envelope=envelope,
        )

    def is_concurrency_safe(self, arguments: dict[str, Any] | BaseModel) -> bool:
        return False
