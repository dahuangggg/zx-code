"""工具执行和模型消息之间使用的工具协议模型。

本模块定义 ToolEnvelope 和 ToolResult，它们是工具实现、ToolCallExecutor、
trace/debug 输出和模型可见工具消息之间的稳定契约。本模块不执行工具，也不做权限判断。
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.model_types.messages import Message


class ToolError(BaseModel):
    """结构化工具错误载荷。

    这个对象只在 ToolEnvelope.status == "error" 时出现。code 面向程序分支
    和测试断言，message 面向模型与 trace 阅读者。
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ToolEnvelope(BaseModel):
    """工具返回协议的唯一规范格式。

    ToolEnvelope 是会进入 role="tool" 消息的内层协议。调用链外层仍然需要
    ToolResult 保存 call_id/name，用来和模型发出的 tool_call 精确配对。

    字段分工：
      data    — 机器可读结果，供后续工具、测试和 trace 解析。
      text    — 给模型看的简短自然语言摘要，尤其适合终端输出或错误说明。
      error   — 仅 error 状态存在，提供稳定错误码。
      stats   — 执行耗时、计数等运行时统计，不应影响业务语义。
      context — cwd、原始参数、文件 mtime 等运行上下文，不应替代 data。
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "partial", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    error: ToolError | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_error_payload(self) -> "ToolEnvelope":
        """保持 status 和 error 的一一对应，避免出现半错误状态。"""
        if self.status == "error" and self.error is None:
            raise ValueError("error status requires error payload")
        if self.status != "error" and self.error is not None:
            raise ValueError("non-error status rejects error payload")
        return self


class ToolResult(BaseModel):
    """一次工具调用的外层结果。

    ToolResult 不再承载业务协议；业务结果统一在 envelope 里。它保留 call_id
    和 name，是为了满足 OpenAI tool message 必须按 tool_call_id 回填的约束，
    也让 debug trace 能定位是哪一次调用产生了这个 envelope。

    content/status/is_error 是兼容属性，给原有代码和测试平滑迁移使用。新代码应优先
    读取 result.envelope。
    """

    model_config = ConfigDict(extra="forbid")
    _PROTOCOL_STATUSES: ClassVar[frozenset[str]] = frozenset({"success", "partial", "error"})

    call_id: str
    name: str
    envelope: ToolEnvelope

    @classmethod
    def _coerce_compat_result_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        """把兼容字段 content/status/is_error 规范化为 envelope。

        插件、MCP 以及部分外部调用可能直接构造 ToolResult(content=...)。这里不能
        简单把任意 JSON dict 当成协议，否则插件返回 {"foo": "bar"} 会被 Pydantic
        当作非法 ToolEnvelope。只有带合法 status 的 dict 才按协议解析；其他 dict
        放入 data.raw，并保留原始文本给模型读取。
        """
        existing_envelope = value.get("envelope")
        if isinstance(existing_envelope, ToolEnvelope):
            envelope_data: dict[str, Any] = existing_envelope.model_dump()
        elif isinstance(existing_envelope, dict):
            envelope_data = dict(existing_envelope)
        else:
            envelope_data = {}

        status = value.get("status") or envelope_data.get("status")
        is_error = value.get("is_error", False)
        has_content = "content" in value
        raw_content = value.get("content", "")
        if has_content:
            if isinstance(raw_content, str):
                try:
                    parsed = json.loads(raw_content)
                except Exception:
                    parsed = None
            else:
                parsed = raw_content

            if isinstance(parsed, dict):
                parsed_status = parsed.get("status")
                if parsed_status in cls._PROTOCOL_STATUSES:
                    envelope_data.update(parsed)
                else:
                    raw_text = (
                        raw_content
                        if isinstance(raw_content, str)
                        else json.dumps(parsed, ensure_ascii=False)
                    )
                    envelope_data.update(
                        {
                            "status": status
                            if status in cls._PROTOCOL_STATUSES
                            else ("error" if is_error else "success"),
                            "data": {"raw": parsed},
                            "text": raw_text,
                        }
                    )
            else:
                envelope_data.update(
                    {
                        "status": status
                        if status in cls._PROTOCOL_STATUSES
                        else ("error" if is_error else "success"),
                        "text": str(raw_content),
                    }
                )
        else:
            raw_content = envelope_data.get("text", "")

        if status in cls._PROTOCOL_STATUSES:
            envelope_data["status"] = status
        else:
            envelope_data.setdefault("status", "error" if is_error else "success")

        if envelope_data.get("status") == "error" and envelope_data.get("error") is None:
            message = envelope_data.get("text") or str(raw_content)
            envelope_data["error"] = {"code": "EXECUTION_ERROR", "message": message}

        return {
            key: item
            for key, item in value.items()
            if key not in {"content", "status", "is_error"}
        } | {"envelope": envelope_data}

    @model_validator(mode="before")
    @classmethod
    def coerce_compat_content(cls, value: Any) -> Any:
        """接受兼容字段，但内部只保存 ToolEnvelope。"""
        if not isinstance(value, dict):
            return value
        if not {"content", "status", "is_error"} & value.keys():
            return value
        return cls._coerce_compat_result_data(value)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "ToolResult":
        """重新校验 copy(update=...)，防止旧字段绕过 extra="forbid"。

        Pydantic 的默认 model_copy(update=...) 不会重新验证 update 内容。原有调用如果
        传入 content/status/is_error，可能在对象上生成影子字段；这里统一走
        model_validate，让兼容转换仍然生效。
        """
        if update is None:
            return super().model_copy(update=update, deep=deep)
        data = self.model_dump()
        data.update(update)
        return type(self).model_validate(data)

    @property
    def content(self) -> str:
        """兼容旧 provider/message 路径的字符串载荷。"""
        return self.envelope.model_dump_json(exclude_none=True)

    @property
    def status(self) -> Literal["success", "partial", "error"]:
        return self.envelope.status

    @property
    def is_error(self) -> bool:
        return self.envelope.status == "error"

    def to_message(self) -> Message:
        """转换成 role="tool" 消息，content 内放完整 ToolEnvelope JSON。"""
        return Message.tool(
            tool_call_id=self.call_id,
            name=self.name,
            content=self.content,
        )
