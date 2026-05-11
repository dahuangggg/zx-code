from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.file_rules import iter_code_files, resolve_codebase_path
from agent.code_context.models import (
    CodeChunk,
    CodeClearResult,
    CodeIndexStats,
    CodeIndexStatus,
    CodeSearchResult,
)
from agent.code_context.ranker import keyword_search, rrf_fuse
from agent.code_context.splitter import split_file


ProgressCallback = Callable[[dict[str, int | str]], None]


class CodeContextIndexer:
    def __init__(
        self,
        *,
        store: ChromaCodeStore,
        snapshot_dir: str | Path,
        max_result_chars: int = 4000,
        max_total_chars: int = 12000,
        max_chunk_chars: int = 1800,
    ) -> None:
        self.store = store
        self.snapshot_dir = Path(snapshot_dir).expanduser()
        self.max_result_chars = max_result_chars
        self.max_total_chars = max_total_chars
        self.max_chunk_chars = max_chunk_chars
        self._background_tasks: dict[str, asyncio.Task[None]] = {}

    def index_codebase(
        self,
        path: str | Path | None = None,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> CodeIndexStats:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        self._write_status(
            codebase_id,
            {
                "codebase_id": codebase_id,
                "codebase_path": str(root),
                "status": "indexing",
                "phase": "scanning files",
                "percentage": 0,
                "current": 0,
                "total": 0,
            },
        )
        try:
            return self._index_codebase(root, progress_callback=progress_callback)
        except Exception as exc:
            self._write_status(
                codebase_id,
                {
                    "codebase_id": codebase_id,
                    "codebase_path": str(root),
                    "status": "indexfailed",
                    "phase": "failed",
                    "percentage": 0,
                    "error": str(exc),
                },
            )
            raise

    def start_background_index(self, path: str | Path | None = None) -> CodeIndexStatus:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        existing = self._background_tasks.get(codebase_id)
        if existing is not None and not existing.done():
            return self.get_status(root)

        self._write_status(
            codebase_id,
            {
                "codebase_id": codebase_id,
                "codebase_path": str(root),
                "status": "indexing",
                "phase": "queued",
                "percentage": 0,
                "current": 0,
                "total": 0,
            },
        )

        async def _run() -> None:
            try:
                await asyncio.to_thread(self.index_codebase, root)
            except Exception:
                return
            finally:
                self._background_tasks.pop(codebase_id, None)

        task = asyncio.create_task(_run())
        self._background_tasks[codebase_id] = task
        return self.get_status(root)

    async def wait_background_index(self, path: str | Path | None = None) -> None:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        task = self._background_tasks.get(codebase_id)
        if task is not None:
            await task

    def search_code(
        self,
        path: str | Path | None,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[CodeSearchResult]:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        if not self._snapshot_path(codebase_id).exists():
            return []
        candidate_limit = max(top_k * 4, 20)
        vector_results = self.store.search(codebase_id=codebase_id, query=query, top_k=candidate_limit)
        all_documents = self.store.documents_for_codebase(codebase_id)
        keyword_results = keyword_search(query, all_documents, limit=candidate_limit)
        results = self._dedupe_results(
            rrf_fuse([vector_results, keyword_results], limit=candidate_limit)
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

    def _dedupe_results(self, results: list[CodeSearchResult]) -> list[CodeSearchResult]:
        deduped: list[CodeSearchResult] = []
        for result in sorted(results, key=lambda item: item.score, reverse=True):
            overlap_index = _find_overlapping_result(deduped, result)
            if overlap_index is None:
                deduped.append(result)
            elif result.score > deduped[overlap_index].score:
                deduped[overlap_index] = result
        return sorted(deduped, key=lambda item: item.score, reverse=True)

    def get_status(self, path: str | Path | None = None) -> CodeIndexStatus:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        snapshot = self._load_snapshot(codebase_id)
        status = self._load_status(codebase_id)
        if status and status.get("status") in {"indexing", "indexfailed"}:
            return CodeIndexStatus.model_validate(
                {
                    "codebase_id": codebase_id,
                    "codebase_path": str(root),
                    **status,
                }
            )
        if status and status.get("status") == "indexed":
            return CodeIndexStatus.model_validate(
                {
                    "codebase_id": codebase_id,
                    "codebase_path": str(root),
                    **status,
                }
            )
        if not snapshot:
            return CodeIndexStatus(
                codebase_id=codebase_id,
                codebase_path=str(root),
                status="not_found",
            )
        files = snapshot.get("files", {})
        return CodeIndexStatus(
            codebase_id=codebase_id,
            codebase_path=str(root),
            status="indexed",
            indexed_files=len(files),
            total_chunks=sum(int(info.get("chunks", 0)) for info in files.values()),
            indexed_at=str(snapshot.get("indexed_at", "")),
            percentage=100,
            current=len(files),
            total=len(files),
            phase="indexed",
        )

    def clear_index(self, path: str | Path | None = None) -> CodeClearResult:
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        snapshot = self._load_snapshot(codebase_id)
        ids: list[str] = []
        if snapshot:
            for info in snapshot.get("files", {}).values():
                ids.extend(info.get("ids", []))
        deleted = self.store.delete_ids(ids) if ids else self.store.delete_codebase(codebase_id)
        snapshot_path = self._snapshot_path(codebase_id)
        if snapshot_path.exists():
            snapshot_path.unlink()
        status_path = self._status_path(codebase_id)
        if status_path.exists():
            status_path.unlink()
        return CodeClearResult(codebase_id=codebase_id, codebase_path=str(root), deleted_chunks=deleted)

    def _index_codebase(
        self,
        root: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> CodeIndexStats:
        codebase_id = codebase_hash(root)
        old_snapshot = self._load_snapshot(codebase_id)
        old_files: dict[str, Any] = old_snapshot.get("files", {}) if old_snapshot else {}

        current_paths = iter_code_files(root)
        total_files = len(current_paths)
        self._record_progress(
            codebase_id,
            root,
            phase="hashing files",
            current=0,
            total=total_files,
            percentage=5,
            progress_callback=progress_callback,
        )
        current_hashes = {
            file_path.relative_to(root).as_posix(): _hash_file(file_path)
            for file_path in current_paths
        }

        removed = sorted(set(old_files) - set(current_hashes))
        added: list[str] = []
        modified: list[str] = []
        skipped = 0
        next_files: dict[str, Any] = {}

        for relative_path in removed:
            self.store.delete_ids(list(old_files.get(relative_path, {}).get("ids", [])))

        for processed, file_path in enumerate(current_paths, start=1):
            relative_path = file_path.relative_to(root).as_posix()
            file_hash = current_hashes[relative_path]
            old_info = old_files.get(relative_path)
            if old_info and old_info.get("hash") == file_hash:
                skipped += 1
                next_files[relative_path] = old_info
                self._record_progress(
                    codebase_id,
                    root,
                    phase=f"skipped {relative_path}",
                    current=processed,
                    total=total_files,
                    percentage=_progress_percentage(processed, total_files),
                    progress_callback=progress_callback,
                )
                continue

            if old_info:
                modified.append(relative_path)
                self.store.delete_ids(list(old_info.get("ids", [])))
            else:
                added.append(relative_path)

            chunks = split_file(file_path, codebase_path=root, max_chars=self.max_chunk_chars)
            chunk_ids: list[str] = []
            prepared: list[CodeChunk] = []
            for index, chunk in enumerate(chunks):
                chunk_id = _chunk_id(codebase_id, relative_path, index, file_hash)
                chunk_ids.append(chunk_id)
                prepared.append(
                    chunk.model_copy(
                        update={
                            "id": chunk_id,
                            "codebase_id": codebase_id,
                            "codebase_path": str(root),
                            "file_hash": file_hash,
                            "chunk_index": index,
                        }
                    )
                )
            self.store.upsert_chunks(prepared)
            next_files[relative_path] = {
                "hash": file_hash,
                "chunks": len(prepared),
                "language": prepared[0].language if prepared else file_path.suffix.lstrip("."),
                "ids": chunk_ids,
            }
            self._record_progress(
                codebase_id,
                root,
                phase=f"indexed {relative_path}",
                current=processed,
                total=total_files,
                percentage=_progress_percentage(processed, total_files),
                progress_callback=progress_callback,
            )

        snapshot = {
            "codebase_id": codebase_id,
            "codebase_path": str(root),
            "collection": self.store.collection_name,
            "files": dict(sorted(next_files.items())),
            "indexed_at": _now(),
        }
        self._write_snapshot(codebase_id, snapshot)
        total_chunks = sum(int(info.get("chunks", 0)) for info in next_files.values())
        self._write_status(
            codebase_id,
            {
                "codebase_id": codebase_id,
                "codebase_path": str(root),
                "status": "indexed",
                "indexed_files": len(next_files),
                "total_chunks": total_chunks,
                "indexed_at": snapshot["indexed_at"],
                "percentage": 100,
                "current": len(next_files),
                "total": len(next_files),
                "phase": "indexed",
            },
        )
        return CodeIndexStats(
            codebase_id=codebase_id,
            codebase_path=str(root),
            indexed_files=len(next_files),
            total_chunks=total_chunks,
            added_files=len(added),
            modified_files=len(modified),
            removed_files=len(removed),
            skipped_files=skipped,
        )

    def _snapshot_path(self, codebase_id: str) -> Path:
        return self.snapshot_dir / f"{codebase_id}.json"

    def _status_path(self, codebase_id: str) -> Path:
        return self.snapshot_dir / f"{codebase_id}.status.json"

    def _load_snapshot(self, codebase_id: str) -> dict[str, Any]:
        path = self._snapshot_path(codebase_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_status(self, codebase_id: str) -> dict[str, Any]:
        path = self._status_path(codebase_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_snapshot(self, codebase_id: str, snapshot: dict[str, Any]) -> None:
        self._write_json(self._snapshot_path(codebase_id), codebase_id, snapshot)

    def _write_status(self, codebase_id: str, status: dict[str, Any]) -> None:
        self._write_json(self._status_path(codebase_id), codebase_id, status)

    def _write_json(self, path: Path, codebase_id: str, payload: dict[str, Any]) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_path = tempfile.mkstemp(dir=self.snapshot_dir, prefix=f".tmp.{codebase_id}.", suffix=".json")
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

    def _record_progress(
        self,
        codebase_id: str,
        root: Path,
        *,
        phase: str,
        current: int,
        total: int,
        percentage: int,
        progress_callback: ProgressCallback | None,
    ) -> None:
        payload = {
            "codebase_id": codebase_id,
            "codebase_path": str(root),
            "status": "indexing",
            "phase": phase,
            "percentage": percentage,
            "current": current,
            "total": total,
        }
        self._write_status(codebase_id, payload)
        if progress_callback is not None:
            progress_callback(payload)


def codebase_hash(path: str | Path) -> str:
    return hashlib.md5(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:12]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_id(codebase_id: str, relative_path: str, index: int, file_hash: str) -> str:
    raw = f"{codebase_id}:{relative_path}:{index}:{file_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _number_lines(content: str, *, start_line: int) -> str:
    return "\n".join(
        f"{line_no}: {line}"
        for line_no, line in enumerate(content.splitlines(), start=start_line)
    )


def _progress_percentage(current: int, total: int) -> int:
    if total <= 0:
        return 100
    return min(99, 10 + round((current / total) * 89))


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
