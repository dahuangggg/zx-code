from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.state.memory import MemoryRecord, MemoryStore
from agent.models import RuntimeConfig
from agent.prompt import SystemPromptBuilder
from agent.state.todo import TodoManager
from agent.tools import build_default_registry


def test_memory_store_uses_frontmatter_and_renders_prompt(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / ".memory" / "MEMORY.md")
    store.append("Prefer concise Chinese explanations.", source="test")

    text = store.path.read_text(encoding="utf-8")
    rendered = store.render_for_prompt()

    assert text.startswith("---")
    assert "format: agent-memory-v1" in text
    assert "Prefer concise Chinese explanations." in rendered


def test_memory_store_can_save_named_markdown_records(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / ".memory" / "MEMORY.md")
    record = MemoryRecord(
        name="project-rules",
        description="Rules for this repository",
        type="project",
        content="Keep docs out of git.",
    )

    path = store.save_record(record)
    index = store.load_index()

    assert path.name == "project-rules.md"
    assert "description: Rules for this repository" in path.read_text(encoding="utf-8")
    assert "[project-rules](project-rules.md)" in index


def test_todo_manager_persists_items(tmp_path: Path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    item = manager.create("write phase 2 docs", notes="include resume bullets")
    manager.update(item.id, status="in_progress")
    manager.complete(item.id)

    reloaded = TodoManager(tmp_path / "todos.json").list()

    assert reloaded[0].title == "write phase 2 docs"
    assert reloaded[0].status == "completed"


def test_system_prompt_builder_includes_memory_and_todos(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / ".memory" / "MEMORY.md")
    memory.append("Use pathlib for filesystem code.", source="test")
    todos = TodoManager(tmp_path / "todos.json")
    item = todos.create("add SessionStore tests")

    prompt = SystemPromptBuilder(
        project_root=tmp_path,
        memory_store=memory,
        todo_manager=todos,
    ).build(RuntimeConfig(session_id="demo"))

    assert "## Memory" in prompt
    assert "must not override source code" in prompt
    assert "Use pathlib for filesystem code." in prompt
    assert item.id in prompt
    assert "## Runtime" in prompt


def test_system_prompt_builder_includes_runtime_metadata_and_real_tool_index(
    tmp_path: Path,
) -> None:
    registry = build_default_registry()

    prompt = SystemPromptBuilder(project_root=tmp_path).build(
        RuntimeConfig(model="openai/test-model", session_id="demo"),
        tool_schemas=registry.schemas(),
    )

    assert "Current date:" in prompt
    assert re.search(r"Current date: \d{4}-\d{2}-\d{2}", prompt)
    assert "Model: openai/test-model" in prompt
    assert "Platform:" in prompt
    assert "Python:" in prompt
    assert "Available tools:" in prompt
    assert "- read_file:" in prompt
    assert "- write_file:" in prompt
    assert "arguments:" not in prompt
    assert "Prefer wrapped agent tools over raw shell commands" in prompt
    assert "Use tool_search when the tool you need is not currently available" in prompt
    assert "When code_search is available" in prompt


@pytest.mark.asyncio
async def test_todo_tools_update_persistent_manager(tmp_path: Path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    registry = build_default_registry(todo_manager=manager)

    created = await registry.execute(
        "todo_create",
        {"title": "implement context guard"},
        call_id="todo-1",
    )
    todo_id = json.loads(created.content)["id"]

    await registry.execute("todo_complete", {"todo_id": todo_id}, call_id="todo-2")

    assert manager.list()[0].status == "completed"
