"""Sanitize trace payloads before writing audit files."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


class TraceSanitizer:
    """Recursively redact common credentials while preserving replay ids."""

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            for key, item in value.items():
                key_str = str(key)
                if self._is_sensitive_key(key_str):
                    output[key_str] = f"<redacted:{key_str}>"
                else:
                    output[key_str] = self.sanitize(item)
            return output
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self.sanitize(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_string(value)
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        lowered = key.lower()
        if lowered in {"tool_call_id", "call_id"}:
            return False
        return lowered in SENSITIVE_KEYS or any(
            marker in lowered
            for marker in ("api_key", "access_token", "refresh_token", "password")
        )

    def _sanitize_string(self, value: str) -> str:
        value = re.sub(r"/Users/[^/\s]+", "/Users/***", value)
        value = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", value)
        value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", value)
        return value

