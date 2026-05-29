"""CodeContext 混合搜索服务。

本模块负责向量/关键词候选召回、RRF 融合、行范围去重、行号补齐和结果截断。
它不构建或更新索引；写入逻辑属于 pipeline.py。
"""
from __future__ import annotations

from pathlib import Path

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.file_rules import resolve_codebase_path
from agent.code_context.models import CodeSearchResult
from agent.code_context.pipeline import codebase_hash
from agent.code_context.ranker import rrf_fuse
from agent.code_context.snapshot_store import CodebaseSnapshotStore
from agent.code_context.sparse_index import SparseKeywordIndex
from agent.code_context.tokenization import path_tokens, tokenize_code_text


class CodeSearchService:
    """负责候选召回、排序融合、去重和结果截断。"""

    def __init__(
        self,
        *,
        store: ChromaCodeStore,
        sparse_index: SparseKeywordIndex,
        snapshot_store: CodebaseSnapshotStore,
        max_result_chars: int,
        max_total_chars: int,
    ) -> None:
        self.store = store
        self.sparse_index = sparse_index
        self.snapshot_store = snapshot_store
        self.max_result_chars = max_result_chars
        self.max_total_chars = max_total_chars

    def search_code(
        self,
        path: str | Path | None,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[CodeSearchResult]:
        """执行向量 + 关键词混合搜索，并用 RRF 融合结果。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        snapshot = self.snapshot_store.load_snapshot(codebase_id)
        if not snapshot and self.snapshot_store.load_status(codebase_id).get("status") != "indexing":
            return []
        if snapshot and snapshot.get("embedding_function") != self.store.embedding_name:
            return []

        candidate_limit = max(top_k * 4, 20)
        vector_results = [
            result.model_copy(update={"matched_by": ["vector"], "vector_rank": rank})
            for rank, result in enumerate(
                self.store.search(codebase_id=codebase_id, query=query, top_k=candidate_limit),
                start=1,
            )
        ]
        keyword_results = self.sparse_index.search(codebase_id, query, limit=candidate_limit)
        results = self.dedupe_results(
            _rerank_code_results(
                rrf_fuse(
                    [vector_results, keyword_results],
                    limit=candidate_limit,
                    weights=[1.0, 1.4],
                ),
                query,
            )
        )[:top_k]

        output: list[CodeSearchResult] = []
        used = 0
        for result in results:
            numbered = _number_lines(result.content, start_line=result.start_line)
            if len(numbered) > self.max_result_chars:
                numbered = numbered[: self.max_result_chars] + "\n[code search result truncated]"
            if used + len(numbered) > self.max_total_chars:
                break
            used += len(numbered)
            output.append(result.model_copy(update={"content": numbered}))
        return output

    def dedupe_results(self, results: list[CodeSearchResult]) -> list[CodeSearchResult]:
        """移除与更高分结果行范围重叠超过 50% 的结果。"""
        deduped: list[CodeSearchResult] = []
        for result in sorted(results, key=lambda item: item.score, reverse=True):
            overlap_index = _find_overlapping_result(deduped, result)
            if overlap_index is None:
                deduped.append(result)
            elif result.score > deduped[overlap_index].score:
                deduped[overlap_index] = result
        return sorted(deduped, key=lambda item: item.score, reverse=True)


def _number_lines(content: str, *, start_line: int) -> str:
    return "\n".join(
        f"{line_no}: {line}"
        for line_no, line in enumerate(content.splitlines(), start=start_line)
    )


def _find_overlapping_result(
    existing: list[CodeSearchResult],
    candidate: CodeSearchResult,
) -> int | None:
    for index, result in enumerate(existing):
        if result.relative_path != candidate.relative_path:
            continue
        overlap_start = max(result.start_line, candidate.start_line)
        overlap_end = min(result.end_line, candidate.end_line)
        if overlap_start > overlap_end:
            continue
        overlap = overlap_end - overlap_start + 1
        candidate_size = max(1, candidate.end_line - candidate.start_line + 1)
        if overlap / candidate_size > 0.5:
            return index
    return None


def _rerank_code_results(
    results: list[CodeSearchResult],
    query: str,
) -> list[CodeSearchResult]:
    query_terms = set(tokenize_code_text(query))
    if not query_terms:
        return results

    scored: list[tuple[float, CodeSearchResult]] = []
    for result in results:
        path_hit_count = len(query_terms & set(path_tokens(result.relative_path)))
        symbol_hit_count = len(query_terms & set(tokenize_code_text(result.symbol)))
        content_hit_count = len(query_terms & set(tokenize_code_text(result.content)))

        boost = 0.0
        boost += min(path_hit_count, 3) * 0.006
        boost += min(symbol_hit_count, 3) * 0.009
        boost += min(content_hit_count, 4) * 0.002
        if "keyword" in result.matched_by and (path_hit_count or symbol_hit_count):
            boost += 0.01

        multiplier = 1.0
        if _is_test_path(result.relative_path) and not (query_terms & {"test", "tests", "testing"}):
            multiplier *= 0.85
        if _is_doc_path(result.relative_path) and not (query_terms & {"doc", "docs", "readme"}):
            multiplier *= 0.75

        adjusted = (result.score + boost) * multiplier
        scored.append((adjusted, result.model_copy(update={"score": round(adjusted, 4)})))
    return [result for _, result in sorted(scored, key=lambda item: item[0], reverse=True)]


def _is_test_path(relative_path: str) -> bool:
    return relative_path.startswith("tests/") or "/tests/" in relative_path


def _is_doc_path(relative_path: str) -> bool:
    return (
        relative_path in {"README.md", "DEVLOG.md"}
        or relative_path.startswith("docs/")
        or relative_path.endswith(".md")
        or relative_path.endswith(".markdown")
    )
