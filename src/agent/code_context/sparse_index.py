from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from agent.code_context.models import CodeSearchResult


class SparseKeywordIndex:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def replace_codebase(self, codebase_id: str, documents: list[CodeSearchResult]) -> None:
        entries: list[dict[str, Any]] = []
        df: dict[str, int] = {}
        for document in documents:
            terms = _tokenize(document.content)
            term_counts: dict[str, int] = {}
            for term in terms:
                term_counts[term] = term_counts.get(term, 0) + 1
            for term in term_counts:
                df[term] = df.get(term, 0) + 1
            entries.append(
                {
                    "relative_path": document.relative_path,
                    "start_line": document.start_line,
                    "end_line": document.end_line,
                    "language": document.language,
                    "content": document.content,
                    "term_counts": term_counts,
                    "length": len(terms),
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
        path = self._path_for(codebase_id)
        if path.exists():
            path.unlink()

    def exists(self, codebase_id: str) -> bool:
        return self._path_for(codebase_id).exists()

    def search(self, codebase_id: str, query: str, *, limit: int) -> list[CodeSearchResult]:
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
        return self.path / f"{codebase_id}.json"

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
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
    return re.findall(r"[a-z0-9_]+", text.lower())
