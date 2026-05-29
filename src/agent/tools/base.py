"""tools.base — 所有内置工具的执行模板。

工具子类只需要声明 schema/input_model，并在 run() 中返回 ToolEnvelope。
execute() 负责参数校验、异常捕获、耗时统计、原始参数回填和旧返回值兼容。
"""
from __future__ import annotations

import time as _time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from agent.models import ToolEnvelope, ToolError, ToolResult


class ErrorCode(str, Enum):
    """工具层稳定错误码。

    错误码会写入 ToolEnvelope.error.code，供测试、trace 和上层策略识别；
    自然语言错误细节放在 message/text 中。
    """

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
    """构造成功或部分成功 envelope，并保证 context 至少带 cwd。"""
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
    """构造标准错误 envelope，供工具、registry 和权限路径复用。"""
    return ToolEnvelope(
        status="error",
        data={},
        text=message,
        error=ToolError(code=code.value, message=message),
        stats=stats or {"time_ms": 0},
        context={"cwd": "."} | (context or {}),
    )


def build_error_envelope(code: ErrorCode, message: str) -> str:
    """为仍需要字符串错误载荷的非 Tool 上下文保留的兼容辅助函数。"""
    return _build_error_tool_envelope(code, message).model_dump_json(exclude_none=True)


def _normalize_compat_result(raw: str | dict[str, Any], *, call_id: str, name: str) -> ToolEnvelope:
    """把兼容的 str/dict 工具返回值规范化为 ToolEnvelope。"""
    compat_content: str | dict[str, Any]
    compat_content = raw
    return ToolResult(
        call_id=call_id,
        name=name,
        content=compat_content,  # type: ignore[call-arg]
    ).envelope


def _inject_meta(
    envelope: ToolEnvelope,
    *,
    time_ms: int,
    params_input: dict[str, Any],
) -> ToolEnvelope:
    """把执行元数据注入 envelope，但不改变工具本身的业务 data/text。"""
    stats = dict(envelope.stats)
    stats["time_ms"] = time_ms
    context = {"cwd": "."} | dict(envelope.context)
    context["params_input"] = params_input
    return envelope.model_copy(update={"stats": stats, "context": context})


class Tool(ABC):
    """内置工具基类。

    run() 是子类的业务实现点，返回模型可见的 ToolEnvelope。execute() 是统一
    入口，负责把任何工具调用都包成 ToolResult，并保证错误路径也符合协议。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        """返回 OpenAI function tool schema。"""
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
        """构造成功 envelope。data 给程序读，text 给模型读。"""
        return _build_envelope("success", data=data, text=text, extra_context=extra_context, stats=stats)

    def partial(
        self,
        *,
        data: dict[str, Any],
        text: str,
        extra_context: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> ToolEnvelope:
        """构造部分成功 envelope，通常用于截断、分页或只完成一部分输出。"""
        return _build_envelope("partial", data=data, text=text, extra_context=extra_context, stats=stats)

    def error(self, *, code: ErrorCode, message: str) -> ToolEnvelope:
        """构造工具业务错误 envelope。未捕获异常仍由 execute() 转 INTERNAL_ERROR。"""
        return _build_error_tool_envelope(code, message)

    @abstractmethod
    async def run(self, arguments: BaseModel) -> ToolEnvelope:
        """执行工具业务逻辑；参数已经由 execute() 校验成 input_model 实例。"""
        raise NotImplementedError

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        """统一执行入口。

        这里是工具协议的边界：外部传入原始 dict，内部校验后调用 run()，最后返回
        ToolResult(call_id/name/envelope)。所有异常都被转换成 error envelope，避免
        主循环因为单个工具失败而中断。
        """
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
                envelope = _normalize_compat_result(raw, call_id=call_id, name=self.name)
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
        """声明同名工具调用是否可在同一轮并发执行。默认保守串行。"""
        return False
