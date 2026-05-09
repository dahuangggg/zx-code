from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.prompt import SystemPromptBuilder
from agent.state.skills import SkillStore
from agent.tools import build_default_registry


def test_skill_store_lists_names_and_loads_markdown(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "review.md").write_text(
        "---\n"
        "description: Review code for regressions first.\n"
        "---\n"
        "# Code Review\n\n"
        "Find bugs before style issues.\n",
        encoding="utf-8",
    )

    store = SkillStore(skills_dir)
    metadata = store.list()
    document = store.load("review")

    assert [item.name for item in metadata] == ["review"]
    assert metadata[0].description == "Review code for regressions first."
    assert document.content.startswith("# Code Review")


def test_prompt_builder_includes_skill_index_not_full_body(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "debug.md").write_text(
        "---\ndescription: Debug by reproducing first.\n---\n"
        "# Debugging\n\n"
        "This full body should be loaded on demand.\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("Project rule: keep edits narrow.\n", encoding="utf-8")

    prompt = SystemPromptBuilder(
        project_root=tmp_path,
        skill_store=SkillStore(skills_dir),
    ).build()

    assert "## Skills" in prompt
    assert "debug" in prompt
    assert "Debug by reproducing first." in prompt
    assert "This full body should be loaded on demand." not in prompt
    assert "## Project Instructions" in prompt
    assert "Project rule: keep edits narrow." in prompt


@pytest.mark.asyncio
async def test_load_skill_tool_returns_skill_content(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "plan.md").write_text("# Planning\n\nWrite a short plan.\n", encoding="utf-8")
    registry = build_default_registry(skill_store=SkillStore(skills_dir))

    result = await registry.execute(
        "load_skill",
        {"name": "plan"},
        call_id="skill-1",
    )
    payload = json.loads(result.content)

    assert not result.is_error
    assert payload["name"] == "plan"
    assert "Write a short plan." in payload["content"]
