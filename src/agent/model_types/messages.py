"""模型和 provider 对话使用的消息模型。

本模块定义聊天消息、模型发出的工具调用请求和单轮模型输出。工具执行结果属于
tool_protocol.py，运行时状态属于 runtime.py。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.model_types.metrics import TokenUsage


class ToolCall(BaseModel):
    """LLM 在一次推理中请求调用的单个工具。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """一条对话消息，对应 OpenAI messages 列表中的一项。

    使用类方法工厂而非直接构造，代码更清晰：
      Message.user("hello")
      Message.assistant("hi", tool_calls=[...])
      Message.tool(call_id, name, result_text)
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
    ) -> "Message":
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
        )

    @classmethod
    def tool(cls, tool_call_id: str, name: str, content: str) -> "Message":
        return cls(
            role="tool",
            tool_call_id=tool_call_id,
            name=name,
            content=content,
        )


class ModelTurn(BaseModel):
    """一次 LLM 推理的完整输出结果。

    stop_reason 常见值：
      "end_turn"   — 模型正常结束
      "tool_use"   — 模型请求调用工具（此时 tool_calls 非空）
      "length"     — 因输出 token 上限被截断（需要 continuation 恢复）
      "max_tokens" — 同上，不同模型的叫法
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: TokenUsage | None = None
