"""运行时配置、状态和结果模型。

本模块定义 core.loop 消费的精简模型。它不解析 TOML 或环境变量；
config.sections 会从 AgentSettings 构建这些值。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.model_types.messages import Message
from agent.model_types.metrics import RunMetrics
from agent.model_types.tool_protocol import ToolResult


class RuntimeConfig(BaseModel):
    """Agent 运行时的不可变配置快照。

    由 AgentSettings.to_runtime_config() 生成，传入 run_task()。
    与 AgentSettings 的区别：RuntimeConfig 是精简版，只含 core/loop 需要的字段；
    AgentSettings 包含 CLI、环境变量和可选扩展能力的完整配置项。
    """

    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-5.4"
    system_prompt: str = ""
    max_iterations: int = 8
    model_timeout_s: float = 60.0
    stream: bool = True
    session_id: str = "default"
    data_dir: str = ".zx-code/state"
    context_max_tokens: int = 12000
    context_keep_recent: int = 6
    context_tool_result_max_chars: int = 6000
    memory_path: str = ".zx-code/state/memory/MEMORY.md"
    enable_memory: bool = True
    enable_todos: bool = True
    debug_log_enabled: bool = False
    debug_log_path: str = ".zx-code/logs/debug.jsonl"
    trace_enabled: bool = False
    trace_dir: str = ".zx-code/logs/traces"
    trace_sanitize: bool = True
    trace_html: bool = False
    trace_html_include_raw_response: bool = False


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
    """``core.loop.run_task`` 返回的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    final_text: str
    iterations: int
    messages: list[Message]
    tool_results: list[ToolResult]
    session_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
