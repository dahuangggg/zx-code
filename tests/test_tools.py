from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.tools import build_default_registry


@pytest.mark.asyncio
async def test_write_then_read_then_edit(tmp_path: Path) -> None:
    registry = build_default_registry()
    file_path = tmp_path / "note.txt"

    write_result = await registry.execute(
        "write_file",
        {
            "path": str(file_path),
            "content": "hello\nworld\n",
        },
        call_id="write-1",
    )
    assert not write_result.is_error

    read_result = await registry.execute(
        "read_file",
        {
            "path": str(file_path),
            "start_line": 1,
            "end_line": 2,
        },
        call_id="read-1",
    )
    read_payload = json.loads(read_result.content)
    assert "1: hello" in read_payload["content"]

    edit_result = await registry.execute(
        "edit_file",
        {
            "path": str(file_path),
            "old_text": "world",
            "new_text": "agent",
        },
        call_id="edit-1",
    )
    assert not edit_result.is_error
    assert file_path.read_text() == "hello\nagent\n"


@pytest.mark.asyncio
async def test_read_file_supports_documented_file_path_offset_limit(tmp_path: Path) -> None:
    registry = build_default_registry()
    file_path = tmp_path / "note.txt"
    file_path.write_text("zero\none\ntwo\nthree\n", encoding="utf-8")

    result = await registry.execute(
        "read_file",
        {
            "file_path": str(file_path),
            "offset": 1,
            "limit": 2,
        },
        call_id="read-docs-shape",
    )
    payload = json.loads(result.content)

    assert not result.is_error
    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert "2: one" in payload["content"]
    assert "3: two" in payload["content"]


def test_read_and_grep_tools_are_concurrency_safe() -> None:
    registry = build_default_registry()

    assert registry.get("read_file").is_concurrency_safe({}) is True
    assert registry.get("grep").is_concurrency_safe({}) is True
    assert registry.get("write_file").is_concurrency_safe({}) is False


@pytest.mark.asyncio
async def test_tool_search_returns_schema_and_activates_matches() -> None:
    registry = build_default_registry()

    assert [schema["function"]["name"] for schema in registry.active_schemas()] == [
        "tool_search"
    ]

    result = await registry.execute(
        "tool_search",
        {"query": "read text file line range", "limit": 3},
        call_id="search-1",
    )
    payload = json.loads(result.content)

    assert not result.is_error
    assert "read_file" in payload["activated"]
    assert any(tool["name"] == "read_file" for tool in payload["tools"])
    assert "read_file" in [
        schema["function"]["name"] for schema in registry.active_schemas()
    ]


@pytest.mark.asyncio
async def test_grep_returns_matches(tmp_path: Path) -> None:
    registry = build_default_registry()
    target = tmp_path / "sample.py"
    target.write_text("print('agent')\nprint('other')\n", encoding="utf-8")

    result = await registry.execute(
        "grep",
        {
            "pattern": "agent",
            "path": str(tmp_path),
        },
        call_id="grep-1",
    )
    payload = json.loads(result.content)
    assert any("agent" in line for line in payload["matches"])


@pytest.mark.asyncio
async def test_bash_executes_command() -> None:
    registry = build_default_registry()
    result = await registry.execute(
        "bash",
        {
            "command": "python3 -c \"print('ok')\"",
        },
        call_id="bash-1",
    )
    payload = json.loads(result.content)
    assert payload["exit_code"] == 0
    assert payload["stdout"].strip() == "ok"


@pytest.mark.asyncio
async def test_validation_errors_are_readable(tmp_path: Path) -> None:
    registry = build_default_registry()
    result = await registry.execute(
        "write_file",
        {
            "path": str(tmp_path / "bad.txt"),
        },
        call_id="write-bad",
    )
    assert result.is_error
    assert "invalid arguments" in result.content
