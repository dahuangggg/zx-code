from __future__ import annotations

from agent.recovery import classify_error


def test_classify_error_identifies_recoverable_model_failures() -> None:
    assert classify_error(RuntimeError("rate limit 429")) == "rate_limit"
    assert classify_error(RuntimeError("invalid api key")) == "auth"
    assert classify_error(RuntimeError("insufficient_quota: billing hard limit")) == "billing"
    assert classify_error(RuntimeError("maximum context length exceeded")) == "overflow"
    assert classify_error(TimeoutError("deadline exceeded")) == "timeout"
    assert classify_error(RuntimeError("unexpected parser bug")) == "unknown"
