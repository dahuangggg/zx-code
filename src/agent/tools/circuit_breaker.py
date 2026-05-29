"""tools.circuit_breaker — session 级按工具隔离的失败熔断器。"""
from __future__ import annotations

import time
from dataclasses import dataclass


FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT_S = 300.0


@dataclass
class _State:
    """单个工具的熔断状态。"""

    consecutive_failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    """按工具名追踪连续失败，并在冷却期内阻止继续执行。

    这个 breaker 是进程内、session 级的轻量保护，不持久化。它防止模型在同一轮
    或连续几轮里反复调用明显故障的工具。
    """

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        recovery_timeout_s: float = RECOVERY_TIMEOUT_S,
    ) -> None:
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_s
        self._states: dict[str, _State] = {}

    def _state(self, tool_name: str) -> _State:
        """惰性创建某个工具的状态槽。"""
        if tool_name not in self._states:
            self._states[tool_name] = _State()
        return self._states[tool_name]

    def is_open(self, tool_name: str) -> bool:
        """返回工具是否仍在熔断冷却期；超时后自动关闭。"""
        state = self._state(tool_name)
        if state.opened_at is None:
            return False
        if time.monotonic() - state.opened_at >= self._timeout:
            state.opened_at = None
            state.consecutive_failures = 0
            return False
        return True

    def record_success(self, tool_name: str) -> None:
        """成功执行后清零失败计数并关闭熔断。"""
        state = self._state(tool_name)
        state.consecutive_failures = 0
        state.opened_at = None

    def record_failure(self, tool_name: str) -> None:
        """记录失败；达到阈值时打开熔断。"""
        state = self._state(tool_name)
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._threshold:
            state.opened_at = time.monotonic()
