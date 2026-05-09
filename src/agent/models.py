"""models — 全局共享的 Pydantic 数据模型。

这是依赖层最底层的模块，几乎所有其他模块都会导入它。
不得在此处导入任何其他 agent.* 模块，以防循环依赖。

主要模型：
  Message       — 一条对话消息（system / user / assistant / tool）
  ToolCall      — LLM 请求调用的工具及其参数
  ToolResult    — 工具执行结果，可转回 Message
  ModelTurn     — 一次 LLM 推理的完整输出
  RuntimeConfig — 运行时不可变配置（由 AgentSettings.to_runtime_config() 产生）
  AgentState    — 单次任务的可变运行状态（消息历史、轮次计数等）
  AgentRunResult— run_task() 的最终返回值
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DMScope = Literal[
    "per-account-channel-peer",  # 每个账号+频道+用户独立会话（最严格）
    "per-channel-peer",           # 同一频道同一用户共享会话
    "per-peer",                   # 跨频道同一用户共享会话
    "per-agent",                  # 所有用户共享同一全局会话（最宽松）
]
"""DM 会话隔离粒度，影响 build_session_key() 的计算方式。"""


class ToolCall(BaseModel):
    """LLM 在一次推理中请求调用的单个工具。"""
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """一条对话消息，对应 LiteLLM / OpenAI messages 列表中的一项。

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


class ToolResult(BaseModel):
    """工具执行完毕后的结果，可用 to_message() 转回 tool 角色的 Message。"""

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


class RuntimeConfig(BaseModel):
    """Agent 运行时的不可变配置快照。

    由 AgentSettings.to_runtime_config() 生成，传入 run_task()。
    与 AgentSettings 的区别：RuntimeConfig 是精简版，只含 core/loop 需要的字段；
    AgentSettings 包含所有 CLI / 环境变量可配置项（包括频道、Webhook 等）。
    """

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
    """一次任务执行期间的可变运行状态。

    messages 列表随对话推进不断增长，是上下文压缩的输入。
    turn_count 每次 LLM 调用后递增，达到 max_iterations 时抛出 MaxIterationsExceededError。
    """

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
