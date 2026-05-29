"""CodeContext 增量索引管线。

本模块负责单个代码库的文件扫描、hash、切块、向量写入、稀疏索引刷新和
snapshot/status 更新。面向工具的公共 API 仍由 CodeContextIndexer 提供。
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.file_rules import iter_code_files
from agent.code_context.job_coordinator import IndexJobCoordinator
from agent.code_context.models import CodeChunk, CodeIndexStats
from agent.code_context.snapshot_store import CodebaseSnapshotStore
from agent.code_context.sparse_index import SparseKeywordIndex
from agent.code_context.splitter import split_file


ProgressCallback = Callable[[dict[str, int | str]], None]


class CodeIndexPipeline:
    """负责增量文件扫描、切块、向量写入和 snapshot 更新。"""

    def __init__(
        self,
        *,
        store: ChromaCodeStore,
        sparse_index: SparseKeywordIndex,
        snapshot_store: CodebaseSnapshotStore,
        jobs: IndexJobCoordinator,
        max_chunk_chars: int,
    ) -> None:
        self.store = store
        self.sparse_index = sparse_index
        self.snapshot_store = snapshot_store
        self.jobs = jobs
        self.max_chunk_chars = max_chunk_chars

    def index_codebase(
        self,
        root: Path,
        *,
        progress_callback: ProgressCallback | None = None,
        custom_extensions: list[str] | set[str] | None = None,
        ignore_patterns: list[str] | set[str] | None = None,
    ) -> CodeIndexStats:
        codebase_id = codebase_hash(root)
        with self.jobs.lock_for(codebase_id):
            with self.jobs.index_file_lock(codebase_id):
                return self._index_codebase_locked(
                    root,
                    progress_callback=progress_callback,
                    custom_extensions=custom_extensions,
                    ignore_patterns=ignore_patterns,
                )

    def _index_codebase_locked(
        self,
        root: Path,
        *,
        progress_callback: ProgressCallback | None = None,
        custom_extensions: list[str] | set[str] | None = None,
        ignore_patterns: list[str] | set[str] | None = None,
    ) -> CodeIndexStats:
        codebase_id = codebase_hash(root)
        self.jobs.check_cancelled(codebase_id)
        old_snapshot = self.snapshot_store.load_snapshot(codebase_id)
        old_files: dict[str, Any] = old_snapshot.get("files", {}) if old_snapshot else {}
        embedding_function = self.store.embedding_name
        if old_snapshot and old_snapshot.get("embedding_function") != embedding_function:
            self.store.delete_codebase(codebase_id)
            old_files = {}

        current_paths = iter_code_files(
            root,
            custom_extensions=custom_extensions,
            custom_ignore_patterns=ignore_patterns,
        )
        self.jobs.check_cancelled(codebase_id)
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
        current_hashes = {}
        for file_path in current_paths:
            self.jobs.check_cancelled(codebase_id)
            current_hashes[file_path.relative_to(root).as_posix()] = _hash_file(file_path)

        removed = sorted(set(old_files) - set(current_hashes))
        added: list[str] = []
        modified: list[str] = []
        skipped = 0
        next_files: dict[str, Any] = {}

        for relative_path in removed:
            self.jobs.check_cancelled(codebase_id)
            self.store.delete_ids(list(old_files.get(relative_path, {}).get("ids", [])))

        for processed, file_path in enumerate(current_paths, start=1):
            self.jobs.check_cancelled(codebase_id)
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
            self.jobs.check_cancelled(codebase_id)
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

        self.jobs.check_cancelled(codebase_id)
        snapshot = {
            "codebase_id": codebase_id,
            "codebase_path": str(root),
            "collection": self.store.collection_name,
            "embedding_function": embedding_function,
            "custom_extensions": sorted(custom_extensions or []),
            "ignore_patterns": sorted(ignore_patterns or []),
            "files": dict(sorted(next_files.items())),
            "indexed_at": _now(),
        }
        self.snapshot_store.write_snapshot(codebase_id, snapshot)
        self.sparse_index.replace_codebase(codebase_id, self.store.documents_for_codebase(codebase_id))
        total_chunks = sum(int(info.get("chunks", 0)) for info in next_files.values())
        self.snapshot_store.write_status(
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
        self.snapshot_store.write_status(codebase_id, payload)
        if progress_callback is not None:
            progress_callback(payload)


def codebase_hash(path: str | Path) -> str:
    """把绝对代码库路径映射为短 id。"""
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


def _progress_percentage(current: int, total: int) -> int:
    if total <= 0:
        return 100
    return min(99, 10 + round((current / total) * 89))
