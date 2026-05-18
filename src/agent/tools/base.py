"""tools.base — abstract base class for all tools."""
from __future__ import annotations

import json
import time as _time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from agent.models import ToolResult


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


def build_error_envelope(code: ErrorCode, message: str) -> str:
    """Standalone helper for non-Tool contexts (registry, circuit breaker)."""
    return json.dumps(
        {
            "status": "error",
            "data": {},
            "text": message,
            "error": {"code": code.value, "message": message},
            "stats": {"time_ms": 0},
            "context": {"cwd": "."},
        },
        ensure_ascii=False,
    )


def _build_envelope(
    status: str,
    *,
    data: dict[str, Any],
    text: str,
    extra_context: dict[str, Any] | None,
    stats: dict[str, Any] | None,
) -> str:
    context: dict[str, Any] = {"cwd": "."}
    if extra_context:
        context.update(extra_context)
    return json.dumps(
        {
            "status": status,
            "data": data,
            "text": text,
            "stats": stats or {},
            "context": context,
        },
        ensure_ascii=False,
    )


def _inject_meta(content: str, *, time_ms: int, params_input: dict[str, Any]) -> str:
    try:
        env = json.loads(content)
        env.setdefault("stats", {})["time_ms"] = time_ms
        env.setdefault("context", {}).setdefault("params_input", params_input)
        return json.dumps(env, ensure_ascii=False)
    except Exception:
        return content


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
    ) -> str:
        return _build_envelope("success", data=data, text=text, extra_context=extra_context, stats=stats)

    def partial(
        self,
        *,
        data: dict[str, Any],
        text: str,
        extra_context: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> str:
        return _build_envelope("partial", data=data, text=text, extra_context=extra_context, stats=stats)

    def error(self, *, code: ErrorCode, message: str) -> str:
        return build_error_envelope(code, message)

    @abstractmethod
    async def run(self, arguments: BaseModel) -> str | dict[str, Any]:
        raise NotImplementedError

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        t0 = _time.monotonic()
        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            content = build_error_envelope(
                ErrorCode.INVALID_PARAM,
                f"invalid arguments for {self.name}: {exc.errors(include_url=False)}",
            )
            return ToolResult(call_id=call_id, name=self.name, content=content, status="error", is_error=True)
        try:
            raw = await self.run(parsed)
            # Backward compat: tools not yet migrated to envelope API may return dict
            if isinstance(raw, dict):
                content = json.dumps(raw, ensure_ascii=False)
            else:
                content = raw
        except Exception as exc:
            content = build_error_envelope(
                ErrorCode.INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
            )
            return ToolResult(call_id=call_id, name=self.name, content=content, status="error", is_error=True)
        elapsed_ms = round((_time.monotonic() - t0) * 1000)
        content = _inject_meta(content, time_ms=elapsed_ms, params_input=arguments)
        try:
            status = json.loads(content).get("status", "success")
        except Exception:
            status = "success"
        return ToolResult(
            call_id=call_id,
            name=self.name,
            content=content,
            status=status,
            is_error=(status == "error"),
        )

    def is_concurrency_safe(self, arguments: dict[str, Any] | BaseModel) -> bool:
        return False
