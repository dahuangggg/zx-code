from __future__ import annotations

import time
import pytest
from agent.tools.circuit_breaker import CircuitBreaker


def test_initially_closed():
    cb = CircuitBreaker()
    assert not cb.is_open("tool_a")


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=300.0)
    cb.record_failure("tool_a")
    cb.record_failure("tool_a")
    assert not cb.is_open("tool_a")
    cb.record_failure("tool_a")
    assert cb.is_open("tool_a")


def test_success_resets_counter():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_s=300.0)
    cb.record_failure("tool_a")
    cb.record_failure("tool_a")
    cb.record_success("tool_a")
    cb.record_failure("tool_a")
    cb.record_failure("tool_a")
    assert not cb.is_open("tool_a")
    cb.record_failure("tool_a")
    assert cb.is_open("tool_a")


def test_recovers_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=0.01)
    cb.record_failure("tool_a")
    assert cb.is_open("tool_a")
    time.sleep(0.02)
    assert not cb.is_open("tool_a")


def test_independent_per_tool():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=300.0)
    cb.record_failure("tool_a")
    assert cb.is_open("tool_a")
    assert not cb.is_open("tool_b")


def test_success_clears_open_circuit():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_s=300.0)
    cb.record_failure("tool_a")
    assert cb.is_open("tool_a")
    cb.record_success("tool_a")
    assert not cb.is_open("tool_a")
