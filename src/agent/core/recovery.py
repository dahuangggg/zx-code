"""core.recovery — LLM 调用错误分类与弹性恢复（s11）。

提供两个核心组件：

RecoveryBudget
    按分类（continuation / compaction / backoff）追踪已用重试次数，
    防止无限循环。默认每类最多 3 次。

ResilienceRunner
    对单次 LLM 调用的弹性包装，根据 ``classify_error()`` 的分类采取不同策略：
      - rate_limit  → 指数退避重试（backoff budget）
      - overflow    → 调用 compact_fn() 压缩历史后重试（compaction budget）
      - length      → 追加"请继续"消息后重试（continuation budget）
      - timeout     → 直接抛出 ModelTimeoutError
      - auth/billing→ 直接抛出 ModelInvocationError（不重试）

``classify_error()`` 通过字符串匹配分类各 LLM SDK 的异常，
与具体供应商解耦（litellm 的异常格式各异，此函数统一处理）。
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent.errors import (
    AgentError,
    ContextOverflowError,
    ModelInvocationError,
    ModelTimeoutError,
)
from agent.models import Message, ModelTurn
from agent.providers.base import ModelClient

# 压缩函数类型：接收消息列表，返回压缩后的消息列表
CompactFn = Callable[[list[Message]], Awaitable[list[Message]]]


class RecoveryBudget:
    """Track per-category retry attempts to prevent infinite loops.

    分类说明：
      - continuation / compaction：单次 LLM 调用内的重试预算，每轮应通过 reset_turn() 重置
      - backoff：跨轮全局限流预算，不重置（累计反映实际限流压力）
    """

    def __init__(self, max_retries: int = 3) -> None:
        self.max = max_retries
        self.attempts: dict[str, int] = {
            "continuation": 0,
            "compaction": 0,
            "backoff": 0,
        }

    def can_retry(self, category: str) -> bool:
        return self.attempts.get(category, 0) < self.max

    def record(self, category: str) -> None:
        self.attempts[category] = self.attempts.get(category, 0) + 1

    def reset_turn(self) -> None:
        """每个新 agent 循环轮次开始前调用，重置轮次内预算，保留跨轮的 backoff 计数。"""
        self.attempts["continuation"] = 0
        self.attempts["compaction"] = 0

    def backoff_delay(self) -> float:
        # random.random() 加抖动避免多请求同时重试造成请求风暴；上限 60s 防止等待过久
        return min(2 ** self.attempts.get("backoff", 0) + random.random(), 60.0)


SleepFunc = Callable[[float], Awaitable[None]]


def classify_error(exc: Exception) -> str:
    """Classify an LLM error for recovery routing."""
    # 同时检查异常 message 和类型名：不同 SDK 的异常结构各异，统一靠字符串匹配做兜底分类
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    if isinstance(exc, TimeoutError) or "timeout" in exc_type:
        return "timeout"
    if any(term in msg for term in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"

    if any(term in msg for term in ("rate_limit", "rate limit", "429", "too many requests")):
        return "rate_limit"
    if any(term in msg for term in ("ratelimit",)):
        return "rate_limit"
    if any(term in exc_type for term in ("ratelimit",)):
        return "rate_limit"

    if any(term in msg for term in (
        "invalid api key", "invalid_api_key", "unauthorized", "401",
        "forbidden", "403", "authentication", "permission denied",
    )):
        return "auth"
    if any(term in exc_type for term in ("auth", "permission")):
        return "auth"

    if any(term in msg for term in (
        "billing", "insufficient_quota", "quota exceeded", "payment required",
        "credit", "balance",
    )):
        return "billing"

    # overflow = 输入超长（需要压缩历史）：关键词明确指向"上下文/输入 token 超限"
    # 注意：不包含 "max_tokens"——该词同时出现在"输出截断"场景，容易与 continuation 混淆
    if any(term in msg for term in (
        "context_length", "context length", "maximum context",
        "token limit", "too many tokens",
        "context window", "exceeds the model",
    )):
        return "overflow"

    return "unknown"


class ResilienceRunner:
    """Run one model turn with bounded recovery strategies.

    compact_fn 参数接受 ContextGuard.compact_history 或任何签名兼容的函数，
    与 ContextGuard 具体实现解耦，便于测试和替换压缩策略。
    """

    def __init__(
        self,
        *,
        model_client: ModelClient,
        timeout_s: float,
        recovery_budget: RecoveryBudget | None = None,
        compact_fn: CompactFn | None = None,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self.model_client = model_client
        self.timeout_s = timeout_s
        self.recovery_budget = recovery_budget or RecoveryBudget()
        self.compact_fn = compact_fn
        self.sleep = sleep

    async def run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: Callable[[str], Any] | None = None,
    ) -> ModelTurn:
        current_messages = list(messages)
        # 跨多轮 continuation 累积输出片段，最终拼接成完整回复返回给上层
        continued_text_parts: list[str] = []

        while True:
            try:
                turn = await asyncio.wait_for(
                    self.model_client.run_turn(
                        system_prompt=system_prompt,
                        messages=current_messages,
                        tools=tools,
                        stream_handler=stream_handler,
                    ),
                    timeout=self.timeout_s,
                )

                # 输出被截断且没工具调用：把已生成的 assistant 文本回灌，再追加一条 user 指令请求续写
                # 要求 turn.text 非空：空文本续写只会浪费一次调用，且可能触发同样的空响应死循环
                if self._should_continue(turn):
                    self.recovery_budget.record("continuation")
                    continued_text_parts.append(turn.text)
                    current_messages = [
                        *current_messages,
                        Message.assistant(turn.text),
                        Message.user(
                            "[Your response was truncated. Please continue from where you left off.]"
                        ),
                    ]
                    continue

                if continued_text_parts:
                    return turn.model_copy(
                        update={"text": "".join([*continued_text_parts, turn.text])}
                    )
                return turn

            except TimeoutError as exc:
                raise ModelTimeoutError(
                    f"model turn timed out after {self.timeout_s:.1f}s"
                ) from exc

            # AgentError 是框架自定义异常（如 ContextOverflowError），已分类过，直接向上抛
            except AgentError:
                raise

            except Exception as exc:
                error_type = classify_error(exc)

                if error_type == "rate_limit" and self.recovery_budget.can_retry("backoff"):
                    self.recovery_budget.record("backoff")
                    await self.sleep(self.recovery_budget.backoff_delay())
                    continue

                # 输入超长：调用 compact_fn 压缩历史后重试；无 compact_fn 时无法自愈，直接抛
                if error_type == "overflow" and self.recovery_budget.can_retry("compaction"):
                    self.recovery_budget.record("compaction")
                    if self.compact_fn is not None:
                        current_messages = await self.compact_fn(current_messages)
                        continue
                    raise ContextOverflowError(
                        f"context overflow and no compact_fn available: {exc}"
                    ) from exc

                raise ModelInvocationError(f"model turn failed: {exc}") from exc

    def _should_continue(self, turn: ModelTurn) -> bool:
        # 有 tool_calls 时不续写：工具调用是结构化输出，截断的工具参数无法靠"请继续"安全拼接
        # 要求 turn.text 非空：空文本续写无意义，会浪费预算且可能触发同样的空响应循环
        return (
            turn.stop_reason in ("length", "max_tokens")
            and not turn.tool_calls
            and bool(turn.text)
            and self.recovery_budget.can_retry("continuation")
        )
