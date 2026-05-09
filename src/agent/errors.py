"""errors — Agent 框架的异常层次结构。

所有可预期的运行时错误都继承自 AgentError，便于调用方用一个
``except AgentError`` 捕获所有框架级错误，或按子类精确处理。

层次结构：
  AgentError
  ├── ModelTimeoutError         # LLM 调用超时（timeout_s 配置控制）
  ├── ModelInvocationError      # LLM 返回错误响应（非超时、非限流）
  ├── MaxIterationsExceededError# Agent 循环达到 max_iterations 上限
  ├── ContextOverflowError      # 上下文超出模型窗口且无法压缩
  └── RateLimitError            # LLM 供应商限流（由 ResilienceRunner 自动退避重试）
"""
from __future__ import annotations


class AgentError(RuntimeError):
    """所有 agent 框架级错误的基类。"""


class ModelTimeoutError(AgentError):
    """LLM 调用超过 ``RuntimeConfig.model_timeout_s`` 后抛出。"""


class ModelInvocationError(AgentError):
    """LLM 返回了错误响应（非超时、非限流），通常由网络错误或模型错误触发。"""


class MaxIterationsExceededError(AgentError):
    """Agent 循环在未得到最终文本回复的情况下耗尽了最大迭代次数。"""


class ContextOverflowError(AgentError):
    """消息历史超出模型上下文窗口，且 ContextGuard 无法进一步压缩时抛出。"""


class RateLimitError(AgentError):
    """LLM 供应商返回 429 限流，且 RecoveryBudget 的退避次数已耗尽时抛出。"""
