from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
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


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    content: str
    is_error: bool = False

    def to_message(self) -> Message:
        return Message.tool(
            tool_call_id=self.call_id,
            name=self.name,
            content=self.content,
        )


class ModelTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = "end_turn"


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "openai/gpt-4o-mini"
    system_prompt: str = ""
    max_iterations: int = 8
    model_timeout_s: float = 60.0
    stream: bool = True
    session_id: str = "default"
    data_dir: str = ".agent"
    context_max_tokens: int = 12000
    context_keep_recent: int = 6
    context_tool_result_max_chars: int = 6000
    memory_path: str = ".memory/MEMORY.md"
    enable_memory: bool = True
    enable_todos: bool = True


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    max_iterations: int
    session_id: str | None = None
    turn_count: int = 0
    messages: list[Message] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_text: str
    iterations: int
    messages: list[Message]
    tool_results: list[ToolResult]
    session_id: str | None = None
