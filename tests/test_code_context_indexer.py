from __future__ import annotations

import asyncio
from pathlib import Path

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.indexer import CodeContextIndexer


class DeterministicEmbeddingFunction:
    def __call__(self, input):  # Chroma embedding function protocol
        vectors = []
        for text in input:
            lowered = text.lower()
            vectors.append(
                [
                    float(lowered.count("auth")),
                    float(lowered.count("retry")),
                    float(lowered.count("context")),
                    float(len(lowered) % 17),
                ]
            )
        return vectors


def _indexer(tmp_path: Path) -> CodeContextIndexer:
    store = ChromaCodeStore(
        path=tmp_path / "chroma",
        collection_name="agent_code_context",
        embedding_function=DeterministicEmbeddingFunction(),
    )
    return CodeContextIndexer(
        store=store,
        snapshot_dir=tmp_path / "snapshots",
        max_result_chars=2000,
        max_total_chars=5000,
    )


def test_indexer_indexes_searches_and_reports_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def authenticate_user():\n    return 'auth token'\n",
        encoding="utf-8",
    )

    indexer = _indexer(tmp_path)
    stats = indexer.index_codebase(repo)
    results = indexer.search_code(repo, "auth user", top_k=3)
    status = indexer.get_status(repo)

    assert stats.indexed_files == 1
    assert stats.total_chunks >= 1
    assert status.status == "indexed"
    assert status.indexed_files == 1
    assert results
    assert results[0].relative_path == "auth.py"
    assert "authenticate_user" in results[0].content


def test_indexer_updates_modified_and_removed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "service.py"
    target.write_text("def retry_request():\n    return 'retry'\n", encoding="utf-8")
    old = repo / "old.py"
    old.write_text("def old_context():\n    return 'context'\n", encoding="utf-8")

    indexer = _indexer(tmp_path)
    first = indexer.index_codebase(repo)
    target.write_text("def auth_request():\n    return 'auth'\n", encoding="utf-8")
    old.unlink()
    second = indexer.index_codebase(repo)

    assert first.indexed_files == 2
    assert second.modified_files == 1
    assert second.removed_files == 1
    assert "old.py" not in [result.relative_path for result in indexer.search_code(repo, "old context")]
    assert indexer.search_code(repo, "auth request")[0].relative_path == "service.py"


def test_indexer_clear_removes_local_index_and_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def context_layer():\n    return 'context'\n", encoding="utf-8")

    indexer = _indexer(tmp_path)
    indexer.index_codebase(repo)
    cleared = indexer.clear_index(repo)

    assert cleared.deleted_chunks >= 1
    assert indexer.get_status(repo).status == "not_found"
    assert indexer.search_code(repo, "context") == []


async def test_indexer_background_index_reports_progress_and_completion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for index in range(8):
        (repo / f"file_{index}.py").write_text(
            f"def context_func_{index}():\n    return 'context {index}'\n",
            encoding="utf-8",
        )

    indexer = _indexer(tmp_path)
    started = indexer.start_background_index(repo)

    assert started.status == "indexing"
    assert started.percentage == 0

    for _ in range(100):
        status = indexer.get_status(repo)
        if status.status == "indexed":
            break
        await asyncio.sleep(0.02)

    status = indexer.get_status(repo)
    assert status.status == "indexed"
    assert status.percentage == 100
    assert status.indexed_files == 8


def test_hybrid_search_keyword_channel_can_promote_exact_identifier(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "generic.py").write_text(
        "def generic_context():\n    return 'context context context'\n",
        encoding="utf-8",
    )
    (repo / "payment.py").write_text(
        "def payment_flow():\n    return 'ZX_UNIQUE_PAYMENT_FLOW'\n",
        encoding="utf-8",
    )

    indexer = _indexer(tmp_path)
    indexer.index_codebase(repo)

    results = indexer.search_code(repo, "ZX_UNIQUE_PAYMENT_FLOW", top_k=2)

    assert results[0].relative_path == "payment.py"


def test_hybrid_search_deduplicates_overlapping_line_ranges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(
        "\n".join(
            [
                "def context_service():",
                "    context = 'context'",
                "    return context",
                "",
            ]
        ),
        encoding="utf-8",
    )

    indexer = _indexer(tmp_path)
    indexer.index_codebase(repo)
    codebase_id = indexer.get_status(repo).codebase_id
    base = indexer.store.documents_for_codebase(codebase_id)[0]
    duplicate = base.model_copy(update={"score": base.score + 0.1})

    deduped = indexer._dedupe_results([duplicate, base])

    assert len(deduped) == 1
    assert deduped[0].score == duplicate.score
