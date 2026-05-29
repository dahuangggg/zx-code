"""Code-aware tokenization helpers for CodeContext search."""
from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def tokenize_code_text(text: str) -> list[str]:
    """Return stable search tokens while preserving useful identifier forms."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lowered = raw.lower()
        if lowered:
            tokens.append(lowered)
        for part in raw.split("_"):
            _append_identifier_parts(tokens, part)
    return tokens


def path_tokens(path: str) -> list[str]:
    """Tokenize a repository-relative path into path, stem, and segment tokens."""
    normalized = path.replace("\\", "/")
    tokens = tokenize_code_text(normalized)
    for segment in normalized.split("/"):
        if "." in segment:
            stem, *suffixes = segment.split(".")
            tokens.extend(tokenize_code_text(stem))
            for suffix in suffixes:
                tokens.extend(tokenize_code_text(suffix))
    return tokens


def _append_identifier_parts(tokens: list[str], value: str) -> None:
    if not value:
        return
    for part in _CAMEL_BOUNDARY_RE.split(value):
        lowered = part.lower()
        if lowered:
            tokens.append(lowered)
