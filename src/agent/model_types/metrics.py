"""代理运行时共享的指标和 token 用量模型。

本模块只定义 token 和耗时聚合结构，不负责记录指标或发送 trace 事件；这些工作由
core.loop 和 trace/debug 模块完成。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TokenUsage(BaseModel):
    """一次 LLM 调用的 token 消耗。"""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ToolMetric(BaseModel):
    """单次工具调用的计时。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    call_id: str
    duration_ms: float
    is_error: bool = False


class TurnMetric(BaseModel):
    """单轮 LLM 调用 + 工具执行的指标。"""

    model_config = ConfigDict(extra="forbid")

    turn: int
    llm_duration_ms: float = 0.0
    usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_metrics: list[ToolMetric] = Field(default_factory=list)
    compact_triggered: bool = False


class RunMetrics(BaseModel):
    """整次 run_task 的聚合指标。"""

    model_config = ConfigDict(extra="forbid")

    total_turns: int = 0
    total_llm_duration_ms: float = 0.0
    total_tool_duration_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_tool_calls: int = 0
    turns: list[TurnMetric] = Field(default_factory=list)

    def record_turn(self, turn_metric: TurnMetric) -> None:
        self.turns.append(turn_metric)
        self.total_turns = len(self.turns)
        self.total_llm_duration_ms += turn_metric.llm_duration_ms
        self.total_prompt_tokens += turn_metric.usage.prompt_tokens
        self.total_completion_tokens += turn_metric.usage.completion_tokens
        self.total_tokens += turn_metric.usage.total_tokens
        for tm in turn_metric.tool_metrics:
            self.total_tool_duration_ms += tm.duration_ms
            self.total_tool_calls += 1
