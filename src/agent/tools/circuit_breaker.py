"""tools.circuit_breaker — session-scoped per-tool failure breaker."""
from __future__ import annotations

import time
from dataclasses import dataclass


FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT_S = 300.0


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    """Track consecutive failures per tool; block after threshold until timeout."""

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        recovery_timeout_s: float = RECOVERY_TIMEOUT_S,
    ) -> None:
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_s
        self._states: dict[str, _State] = {}

    def _state(self, tool_name: str) -> _State:
        if tool_name not in self._states:
            self._states[tool_name] = _State()
        return self._states[tool_name]

    def is_open(self, tool_name: str) -> bool:
        state = self._state(tool_name)
        if state.opened_at is None:
            return False
        if time.monotonic() - state.opened_at >= self._timeout:
            state.opened_at = None
            state.consecutive_failures = 0
            return False
        return True

    def record_success(self, tool_name: str) -> None:
        state = self._state(tool_name)
        state.consecutive_failures = 0
        state.opened_at = None

    def record_failure(self, tool_name: str) -> None:
        state = self._state(tool_name)
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._threshold:
            state.opened_at = time.monotonic()
