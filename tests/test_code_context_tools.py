from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.indexer import CodeContextIndexer
from agent.permissions import PermissionManager
from agent.tools import build_default_registry


class DeterministicEmbeddingFunction:
    def __call__(self, input):
        return [[float(text.lower().count("context")), float(len(text) % 11)] for text in input]


def _indexer(tmp_path: Path) -> CodeContextIndexer:
    return CodeContextIndexer(
        store=ChromaCodeStore(
            path=tmp_path / "chroma",
            collection_name="agent_code_context",
            embedding_function=DeterministicEmbeddingFunction(),
        ),
        snapshot_dir=tmp_path / "snapshots",
    )


async def test_code_context_tools_run_through_registry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "context.py").write_text("def code_context():\n    return 'context'\n", encoding="utf-8")
    registry = build_default_registry(code_context_indexer=_indexer(tmp_path))

    indexed = await registry.execute("code_index", {"path": str(repo)}, call_id="idx")
    searched = await registry.execute(
        "code_search",
        {"path": str(repo), "query": "context layer"},
        call_id="search",
    )
    status = await registry.execute("code_index_status", {"path": str(repo)}, call_id="status")

    assert not indexed.is_error
    assert json.loads(indexed.content)["indexed_files"] == 1
    assert json.loads(searched.content)["results"][0]["relative_path"] == "context.py"
    assert json.loads(status.content)["status"] == "indexed"


async def test_code_index_tool_can_start_background_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "context.py").write_text("def code_context():\n    return 'context'\n", encoding="utf-8")
    registry = build_default_registry(code_context_indexer=_indexer(tmp_path))

    started = await registry.execute(
        "code_index",
        {"path": str(repo), "background": True},
        call_id="idx-bg",
    )

    content = json.loads(started.content)
    assert content["status"] in {"indexing", "indexed"}
    assert "percentage" in content
    for _ in range(100):
        status = json.loads(
            (
                await registry.execute(
                    "code_index_status",
                    {"path": str(repo)},
                    call_id="idx-bg-status",
                )
            ).content
        )
        if status["status"] == "indexed":
            break
        await asyncio.sleep(0.02)


async def test_code_index_clear_can_be_denied_by_permissions(tmp_path: Path) -> None:
    registry = build_default_registry(
        permission_manager=PermissionManager(tool_policies={"code_index_clear": "deny"}),
        code_context_indexer=_indexer(tmp_path),
    )

    result = await registry.execute("code_index_clear", {"path": str(tmp_path)}, call_id="clear")

    assert result.is_error
    assert "permission denied" in result.content
