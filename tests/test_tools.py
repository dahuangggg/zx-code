from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.models import ToolResult
from agent.tools import build_default_registry
from agent.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_write_then_read_then_edit(tmp_path: Path) -> None:
    registry = build_default_registry()
    file_path = tmp_path / "note.txt"

    write_result = await registry.execute(
        "write_file",
        {"path": str(file_path), "content": "hello\nworld\n"},
        call_id="write-1",
    )
    assert not write_result.is_error

    read_result = await registry.execute(
        "read_file",
        {"path": str(file_path), "start_line": 1, "end_line": 2},
        call_id="read-1",
    )
    read_payload = json.loads(read_result.content)
    assert "1: hello" in read_payload["data"]["content"]

    edit_result = await registry.execute(
        "edit_file",
        {"path": str(file_path), "old_text": "world", "new_text": "agent"},
        call_id="edit-1",
    )
    assert not edit_result.is_error
    assert file_path.read_text() == "hello\nagent\n"
    edit_data = json.loads(edit_result.content)["data"]
    assert "diff" in edit_data
    assert "-world" in edit_data["diff"]
    assert "+agent" in edit_data["diff"]


@pytest.mark.asyncio
async def test_read_file_supports_documented_file_path_offset_limit(tmp_path: Path) -> None:
    registry = build_default_registry()
    file_path = tmp_path / "note.txt"
    file_path.write_text("zero\none\ntwo\nthree\n", encoding="utf-8")

    result = await registry.execute(
        "read_file",
        {"file_path": str(file_path), "offset": 1, "limit": 2},
        call_id="read-docs-shape",
    )
    assert not result.is_error
    data = json.loads(result.content)["data"]
    assert data["start_line"] == 2
    assert data["end_line"] == 3
    assert "2: one" in data["content"]
    assert "3: two" in data["content"]


def test_read_and_grep_tools_are_concurrency_safe() -> None:
    registry = build_default_registry()
    assert registry.get("read_file").is_concurrency_safe({}) is True
    assert registry.get("grep").is_concurrency_safe({}) is True
    assert registry.get("glob").is_concurrency_safe({}) is True
    assert registry.get("write_file").is_concurrency_safe({}) is False


@pytest.mark.asyncio
async def test_tool_search_returns_schema_and_activates_matches() -> None:
    registry = build_default_registry()

    assert [schema["function"]["name"] for schema in registry.active_schemas()] == [
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "glob",
        "tool_search",
    ]

    result = await registry.execute(
        "tool_search",
        {"query": "read text file line range", "limit": 3},
        call_id="search-1",
    )
    assert not result.is_error
    data = json.loads(result.content)["data"]
    assert "read_file" in data["activated"]
    assert any(tool["name"] == "read_file" for tool in data["tools"])
    assert "read_file" in [schema["function"]["name"] for schema in registry.active_schemas()]


@pytest.mark.asyncio
async def test_tool_search_accepts_display_names() -> None:
    registry = build_default_registry()

    result = await registry.execute(
        "tool_search",
        {"query": "find files", "names": ["Glob"], "limit": 1},
        call_id="search-glob",
    )
    assert not result.is_error
    data = json.loads(result.content)["data"]
    assert data["activated"] == ["glob"]
    assert data["tools"][0]["name"] == "glob"


@pytest.mark.asyncio
async def test_grep_returns_matches(tmp_path: Path) -> None:
    registry = build_default_registry()
    target = tmp_path / "sample.py"
    target.write_text("print('agent')\nprint('other')\n", encoding="utf-8")

    result = await registry.execute(
        "grep",
        {"pattern": "agent", "path": str(tmp_path)},
        call_id="grep-1",
    )
    data = json.loads(result.content)["data"]
    assert any("agent" in line for line in data["matches"])


@pytest.mark.asyncio
async def test_glob_returns_matching_files(tmp_path: Path) -> None:
    registry = build_default_registry()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('agent')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")

    result = await registry.execute(
        "glob",
        {"pattern": "**/*.py", "path": str(tmp_path)},
        call_id="glob-1",
    )
    data = json.loads(result.content)["data"]
    assert data["matches"] == [str(tmp_path / "src" / "app.py")]
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_bash_executes_command() -> None:
    registry = build_default_registry()
    result = await registry.execute(
        "bash",
        {"command": "python3 -c \"print('ok')\""},
        call_id="bash-1",
    )
    data = json.loads(result.content)["data"]
    assert data["exit_code"] == 0
    assert data["stdout"].strip() == "ok"


@pytest.mark.asyncio
async def test_validation_errors_return_error_status(tmp_path: Path) -> None:
    registry = build_default_registry()
    result = await registry.execute(
        "write_file",
        {"path": str(tmp_path / "bad.txt")},  # missing required 'content'
        call_id="write-bad",
    )
    assert result.is_error
    assert result.status == "error"
    assert result.envelope.error is not None
    assert result.envelope.error.code == "INVALID_PARAM"


@pytest.mark.asyncio
async def test_registry_unknown_tool_error_does_not_use_compat_content(monkeypatch) -> None:
    def fail_compat_coercion(*_args, **_kwargs):
        raise AssertionError("registry errors must construct ToolResult(envelope=...)")

    monkeypatch.setattr(ToolResult, "_coerce_compat_result_data", fail_compat_coercion)

    result = await ToolRegistry().execute("missing_tool", {}, call_id="missing-1")

    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_failures() -> None:
    from pydantic import BaseModel

    from agent.tools.base import ErrorCode, Tool
    from agent.tools.registry import ToolRegistry

    class _FailInput(BaseModel):
        pass

    class _AlwaysFailTool(Tool):
        name = "always_fail"
        description = "always fails"
        input_model = _FailInput

        async def run(self, arguments: _FailInput) -> str:
            return self.error(code=ErrorCode.INTERNAL_ERROR, message="intentional")

    registry = ToolRegistry()
    registry.register(_AlwaysFailTool())

    # First 3 calls fail but circuit is still closed
    for i in range(3):
        result = await registry.execute("always_fail", {}, call_id=f"f{i}")
        assert result.is_error
        assert result.envelope.error is not None
        assert result.envelope.error.code == "INTERNAL_ERROR", f"call {i} should be INTERNAL_ERROR"

    # 4th call: circuit is now open
    result = await registry.execute("always_fail", {}, call_id="f3")
    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_circuit_breaker_does_not_open_after_interrupted_failures() -> None:
    """2 failures then a success should reset the counter; circuit stays closed."""
    from pydantic import BaseModel

    from agent.tools.base import ErrorCode, Tool
    from agent.tools.registry import ToolRegistry

    step = {"n": 0}

    class _FlakyInput(BaseModel):
        pass

    class _FlakyTool(Tool):
        name = "flaky"
        description = "fails on steps 1-2, succeeds on step 3+"
        input_model = _FlakyInput

        async def run(self, arguments: _FlakyInput) -> str:
            step["n"] += 1
            if step["n"] <= 2:
                return self.error(code=ErrorCode.INTERNAL_ERROR, message="boom")
            return self.success(data={}, text="ok")

    registry = ToolRegistry()
    registry.register(_FlakyTool())

    # 2 failures (below threshold of 3)
    for _ in range(2):
        r = await registry.execute("flaky", {}, call_id="x")
        assert r.is_error

    # Success resets the consecutive-failure counter
    r = await registry.execute("flaky", {}, call_id="ok")
    assert not r.is_error

    # Circuit should still be closed: the next call succeeds (step=4, always succeeds now)
    r = await registry.execute("flaky", {}, call_id="still-ok")
    assert not r.is_error
    assert json.loads(r.content)["error"]["code"] if r.is_error else "success" == "success"


# ---------------------------------------------------------------------------
# Optimistic locking (conflict detection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_detected_when_file_modified_after_read(tmp_path: Path) -> None:
    import os
    import time

    registry = build_default_registry()
    file_path = tmp_path / "target.txt"
    file_path.write_text("original\n")

    # Read via registry → populates mtime cache
    read_result = await registry.execute(
        "read_file",
        {"path": str(file_path)},
        call_id="read-1",
    )
    assert not read_result.is_error

    # Externally bump the file's mtime (simulates another process writing the file)
    future_mtime = time.time() + 10.0
    os.utime(str(file_path), (future_mtime, future_mtime))

    # edit_file should now detect the conflict
    edit_result = await registry.execute(
        "edit_file",
        {"path": str(file_path), "old_text": "original", "new_text": "modified"},
        call_id="edit-1",
    )
    assert edit_result.is_error
    assert edit_result.envelope.error is not None
    assert edit_result.envelope.error.code == "CONFLICT"
    assert file_path.read_text() == "original\n"  # file must be untouched


@pytest.mark.asyncio
async def test_no_conflict_when_file_unchanged_after_read(tmp_path: Path) -> None:
    registry = build_default_registry()
    file_path = tmp_path / "target.txt"
    file_path.write_text("original\n")

    # Read, then immediately edit — mtime hasn't changed
    await registry.execute("read_file", {"path": str(file_path)}, call_id="read-1")
    edit_result = await registry.execute(
        "edit_file",
        {"path": str(file_path), "old_text": "original", "new_text": "modified"},
        call_id="edit-1",
    )
    assert not edit_result.is_error
    assert file_path.read_text() == "modified\n"


@pytest.mark.asyncio
async def test_write_file_conflict_after_read(tmp_path: Path) -> None:
    import os
    import time

    registry = build_default_registry()
    file_path = tmp_path / "target.txt"
    file_path.write_text("v1\n")

    await registry.execute("read_file", {"path": str(file_path)}, call_id="read-1")

    future_mtime = time.time() + 10.0
    os.utime(str(file_path), (future_mtime, future_mtime))

    write_result = await registry.execute(
        "write_file",
        {"path": str(file_path), "content": "v2\n"},
        call_id="write-1",
    )
    assert write_result.is_error
    assert write_result.envelope.error is not None
    assert write_result.envelope.error.code == "CONFLICT"
