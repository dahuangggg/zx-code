"""CodeContext 本地关键词索引。

SparseKeywordIndex 把每个代码库的 chunk 以 JSON 文件保存成 BM25 风格倒排统计。
它是 Chroma 向量检索的补充：向量适合语义相近，关键词适合精确符号/文件名片段。
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.code_context.models import CodeSearchResult
from agent.code_context.tokenization import path_tokens, tokenize_code_text


class SparseKeywordIndex:
    """按 codebase_id 维护一个轻量 JSON 关键词索引。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def replace_codebase(self, codebase_id: str, documents: list[CodeSearchResult]) -> None:
        """用当前代码库的完整文档集合替换旧关键词索引。"""
        entries: list[dict[str, Any]] = []
        df: dict[str, int] = {}
        for document in documents:
            term_counts = _weighted_term_counts(document)
            for term in term_counts:
                df[term] = df.get(term, 0) + 1
            entries.append(
                {
                    "relative_path": document.relative_path,
                    "start_line": document.start_line,
                    "end_line": document.end_line,
                    "language": document.language,
                    "symbol": document.symbol,
                    "content": document.content,
                    "term_counts": term_counts,
                    "length": sum(term_counts.values()),
                }
            )

        payload = {
            "version": 1,
            "codebase_id": codebase_id,
            "document_count": len(entries),
            "average_length": (
                sum(int(entry["length"]) for entry in entries) / len(entries)
                if entries
                else 0
            ),
            "document_frequency": df,
            "documents": entries,
        }
        self._write_json(self._path_for(codebase_id), payload)

    def delete_codebase(self, codebase_id: str) -> None:
        """删除某个代码库的关键词索引文件。"""
        path = self._path_for(codebase_id)
        if path.exists():
            path.unlink()

    def exists(self, codebase_id: str) -> bool:
        """判断某个代码库是否已有关键词索引。"""
        return self._path_for(codebase_id).exists()

    def search(self, codebase_id: str, query: str, *, limit: int) -> list[CodeSearchResult]:
        """执行 BM25 风格关键词检索，返回带 keyword_rank 的结果。"""
        if limit <= 0:
            return []
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        payload = self._load_json(self._path_for(codebase_id))
        if not payload:
            return []

        doc_count = int(payload.get("document_count", 0))
        avg_len = float(payload.get("average_length", 0) or 0)
        df = payload.get("document_frequency", {})
        scored: list[tuple[float, CodeSearchResult]] = []
        for raw_document in payload.get("documents", []):
            term_counts = raw_document.get("term_counts", {})
            length = int(raw_document.get("length", 0))
            if length <= 0:
                continue
            score = 0.0
            for term in query_terms:
                tf = int(term_counts.get(term, 0))
                if tf == 0:
                    continue
                term_df = int(df.get(term, 0))
                idf = math.log(1 + (doc_count - term_df + 0.5) / (term_df + 0.5))
                denom = tf + 1.2 * (1 - 0.75 + 0.75 * (length / max(avg_len, 1)))
                score += idf * ((tf * 2.2) / denom)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    CodeSearchResult(
                        relative_path=str(raw_document.get("relative_path", "")),
                        start_line=int(raw_document.get("start_line", 1)),
                        end_line=int(raw_document.get("end_line", 1)),
                        language=str(raw_document.get("language", "text")),
                        score=round(score, 4),
                        content=str(raw_document.get("content", "")),
                        symbol=str(raw_document.get("symbol", "")),
                        matched_by=["keyword"],
                    ),
                )
            )

        ordered = sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
        return [
            result.model_copy(update={"keyword_rank": rank})
            for rank, (_, result) in enumerate(ordered, start=1)
        ]

    def _path_for(self, codebase_id: str) -> Path:
        """返回 codebase_id 对应的 JSON 索引文件路径。"""
        return self.path / f"{codebase_id}.json"

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """原子写 JSON，避免进程中断留下半截索引文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".tmp.{path.stem}.", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _tokenize(text: str) -> list[str]:
    """关键词索引用的简单 token 化，偏向代码标识符和英文单词。"""
    return tokenize_code_text(text)


def _weighted_term_counts(document: CodeSearchResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    _add_terms(counts, _tokenize(document.content), weight=1)
    _add_terms(counts, path_tokens(document.relative_path), weight=3)
    stem = Path(document.relative_path).stem
    _add_terms(counts, _tokenize(stem), weight=4)
    if document.symbol:
        _add_terms(counts, _tokenize(document.symbol), weight=5)
    _add_terms(counts, _tokenize(document.language), weight=1)
    return counts


def _add_terms(counts: dict[str, int], terms: list[str], *, weight: int) -> None:
    for term in terms:
        counts[term] = counts.get(term, 0) + weight
