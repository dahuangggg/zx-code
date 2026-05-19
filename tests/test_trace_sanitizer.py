from __future__ import annotations

from agent.trace import TraceSanitizer


def test_trace_sanitizer_redacts_nested_secrets_and_paths() -> None:
    sanitizer = TraceSanitizer()

    payload = sanitizer.sanitize(
        {
            "api_key": "sk-secret-value",
            "authorization": "Bearer abc123",
            "nested": {"token": "secret-token"},
            "path": "/Users/dahuangggg/Github/project/file.py",
        }
    )

    assert payload["api_key"] == "<redacted:api_key>"
    assert payload["authorization"] == "<redacted:authorization>"
    assert payload["nested"]["token"] == "<redacted:token>"
    assert payload["path"] == "/Users/***/Github/project/file.py"


def test_trace_sanitizer_keeps_tool_call_id_for_replay() -> None:
    sanitizer = TraceSanitizer()

    payload = sanitizer.sanitize({"tool_call_id": "call_123", "call_id": "call_456"})

    assert payload == {"tool_call_id": "call_123", "call_id": "call_456"}

