"""CodeContext 索引器门面。

``CodeContextIndexer`` 保持对外 API 稳定，并把持久化、后台任务协调、索引和搜索
委托给更聚焦的服务。索引循环属于 pipeline.py，排序搜索属于 search_service.py。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent.code_context.chroma_store import ChromaCodeStore
from agent.code_context.file_rules import resolve_codebase_path
from agent.code_context.job_coordinator import (
    IndexCancelled,
    IndexJobCoordinator,
    _IndexFileLock,
)
from agent.code_context.models import (
    CodeClearResult,
    CodeIndexStats,
    CodeIndexStatus,
    CodeSearchResult,
)
from agent.code_context.pipeline import (
    ProgressCallback,
    _chunk_id,
    _hash_file,
    _now,
    _progress_percentage,
    codebase_hash,
)
from agent.code_context.search_service import (
    CodeSearchService,
    _find_overlapping_result,
    _number_lines,
)
from agent.code_context.snapshot_store import CodebaseSnapshotStore
from agent.code_context.sparse_index import SparseKeywordIndex


class CodeContextIndexer:
    """代码库索引、状态、清理和搜索的稳定门面。"""

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

        self.snapshot_store = CodebaseSnapshotStore(self.snapshot_dir)
        self.sparse_index = SparseKeywordIndex(self.snapshot_dir / "sparse")
        self.jobs = IndexJobCoordinator(lock_dir=self.snapshot_dir / "locks")
        self.pipeline = self._build_pipeline()
        self.search_service = CodeSearchService(
            store=self.store,
            sparse_index=self.sparse_index,
            snapshot_store=self.snapshot_store,
            max_result_chars=self.max_result_chars,
            max_total_chars=self.max_total_chars,
        )

        # 保留这些属性，兼容仍会检查内部状态的调用方和测试。
        self._background_tasks = self.jobs.background_tasks
        self._cancel_events = self.jobs.cancel_events
        self._codebase_locks = self.jobs.codebase_locks
        self._state_lock = self.jobs.state_lock

    def _build_pipeline(self):
        from agent.code_context.pipeline import CodeIndexPipeline

        return CodeIndexPipeline(
            store=self.store,
            sparse_index=self.sparse_index,
            snapshot_store=self.snapshot_store,
            jobs=self.jobs,
            max_chunk_chars=self.max_chunk_chars,
        )

    def index_codebase(
        self,
        path: str | Path | None = None,
        *,
        progress_callback: ProgressCallback | None = None,
        custom_extensions: list[str] | set[str] | None = None,
        ignore_patterns: list[str] | set[str] | None = None,
    ) -> CodeIndexStats:
        """同步索引代码库并返回索引统计。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        # Keep terminal status writes inside the same lock as clear_codebase.
        with self.jobs.lock_for(codebase_id):
            self.jobs.clear_cancel_event(codebase_id)
            self.snapshot_store.write_status(
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
                return self.pipeline.index_codebase(
                    root,
                    progress_callback=progress_callback,
                    custom_extensions=custom_extensions,
                    ignore_patterns=ignore_patterns,
                )
            except IndexCancelled:
                self.snapshot_store.write_status(
                    codebase_id,
                    {
                        "codebase_id": codebase_id,
                        "codebase_path": str(root),
                        "status": "indexcancelled",
                        "phase": "cancelled",
                        "percentage": 0,
                    },
                )
                raise
            except Exception as exc:
                self.snapshot_store.write_status(
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

    def start_background_index(
        self,
        path: str | Path | None = None,
        *,
        custom_extensions: list[str] | set[str] | None = None,
        ignore_patterns: list[str] | set[str] | None = None,
    ) -> CodeIndexStatus:
        """在 asyncio task 中启动索引；重复的活动请求会幂等返回当前状态。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        existing = self.jobs.task_for(codebase_id)
        if existing is not None and not existing.done():
            return self.get_status(root)

        self.snapshot_store.write_status(
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
                await asyncio.to_thread(
                    self.index_codebase,
                    root,
                    custom_extensions=custom_extensions,
                    ignore_patterns=ignore_patterns,
                )
            except Exception:
                return
            finally:
                self.jobs.pop_task(codebase_id)

        task = asyncio.create_task(_run())
        self.jobs.set_task(codebase_id, task)
        return self.get_status(root)

    async def wait_background_index(self, path: str | Path | None = None) -> None:
        """等待指定代码库的后台索引任务；没有任务时直接返回。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        task = self.jobs.task_for(codebase_id)
        if task is not None:
            await task

    def cancel_index(self, path: str | Path | None = None) -> CodeIndexStatus:
        """设置指定代码库的取消信号。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        self.jobs.cancel(codebase_id)
        return self.get_status(root)

    def search_code(
        self,
        path: str | Path | None,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[CodeSearchResult]:
        return self.search_service.search_code(path, query, top_k=top_k)

    def get_status(self, path: str | Path | None = None) -> CodeIndexStatus:
        """读取当前索引状态，优先使用 status 文件，其次使用 snapshot。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        snapshot = self.snapshot_store.load_snapshot(codebase_id)
        status = self.snapshot_store.load_status(codebase_id)
        if status and status.get("status") in {"indexing", "indexfailed", "indexcancelled"}:
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

    def list_codebases(self) -> list[dict[str, Any]]:
        """列出所有已知代码库索引。"""
        entries: list[dict[str, Any]] = []
        for path in self.snapshot_store.iter_snapshot_paths():
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            files = snapshot.get("files", {})
            codebase_id = str(snapshot.get("codebase_id", path.stem))
            status = self.snapshot_store.load_status(codebase_id)
            entries.append(
                {
                    "codebase_id": codebase_id,
                    "codebase_path": str(snapshot.get("codebase_path", "")),
                    "status": str(status.get("status") or "indexed"),
                    "indexed_at": str(snapshot.get("indexed_at", "")),
                    "indexed_files": len(files),
                    "total_chunks": sum(int(info.get("chunks", 0)) for info in files.values()),
                    "sparse_index": self.sparse_index.exists(codebase_id),
                }
            )
        return entries

    def list_indexes(self) -> list[dict[str, Any]]:
        """现有工具接口使用的兼容别名。"""
        return self.list_codebases()

    def get_index_info(self, path: str | Path | None = None) -> dict[str, Any]:
        """返回单个代码库的状态、collection 和索引规则元数据。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        status = self.get_status(root).model_dump()
        snapshot = self.snapshot_store.load_snapshot(codebase_id)
        return {
            **status,
            "collection": str(snapshot.get("collection", self.store.collection_name)) if snapshot else self.store.collection_name,
            "custom_extensions": list(snapshot.get("custom_extensions", [])) if snapshot else [],
            "ignore_patterns": list(snapshot.get("ignore_patterns", [])) if snapshot else [],
            "sparse_index": self.sparse_index.exists(codebase_id),
        }

    def clear_codebase(self, path: str | Path | None = None) -> CodeClearResult:
        """清理向量索引、snapshot/status 文件和稀疏关键词索引。"""
        root = resolve_codebase_path(path)
        codebase_id = codebase_hash(root)
        self.cancel_index(root)
        with self.jobs.lock_for(codebase_id):
            deleted = self.store.delete_codebase(codebase_id)
            self.snapshot_store.delete_snapshot(codebase_id)
            self.snapshot_store.delete_status(codebase_id)
            self.sparse_index.delete_codebase(codebase_id)
            self.jobs.forget_cancel_event(codebase_id)
            return CodeClearResult(codebase_id=codebase_id, codebase_path=str(root), deleted_chunks=deleted)

    def clear_index(self, path: str | Path | None = None) -> CodeClearResult:
        """现有工具接口使用的兼容别名。"""
        return self.clear_codebase(path)

    def _index_codebase(
        self,
        root: Path,
        *,
        progress_callback: ProgressCallback | None = None,
        custom_extensions: list[str] | set[str] | None = None,
        ignore_patterns: list[str] | set[str] | None = None,
    ) -> CodeIndexStats:
        return self.pipeline.index_codebase(
            root,
            progress_callback=progress_callback,
            custom_extensions=custom_extensions,
            ignore_patterns=ignore_patterns,
        )

    def _lock_for(self, codebase_id: str):
        return self.jobs.lock_for(codebase_id)

    def _cancel_event_for(self, codebase_id: str):
        return self.jobs.cancel_event_for(codebase_id)

    def _check_cancelled(self, codebase_id: str) -> None:
        self.jobs.check_cancelled(codebase_id)

    def _index_file_lock(self, codebase_id: str) -> _IndexFileLock:
        return self.jobs.index_file_lock(codebase_id)

    def _snapshot_path(self, codebase_id: str) -> Path:
        return self.snapshot_store.snapshot_path(codebase_id)

    def _status_path(self, codebase_id: str) -> Path:
        return self.snapshot_store.status_path(codebase_id)

    def _load_snapshot(self, codebase_id: str) -> dict[str, Any]:
        return self.snapshot_store.load_snapshot(codebase_id)

    def _load_status(self, codebase_id: str) -> dict[str, Any]:
        return self.snapshot_store.load_status(codebase_id)

    def _write_snapshot(self, codebase_id: str, snapshot: dict[str, Any]) -> None:
        self.snapshot_store.write_snapshot(codebase_id, snapshot)

    def _write_status(self, codebase_id: str, status: dict[str, Any]) -> None:
        self.snapshot_store.write_status(codebase_id, status)

    def _write_json(self, path: Path, codebase_id: str, payload: dict[str, Any]) -> None:
        self.snapshot_store.write_json(path, codebase_id, payload)

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
        self.pipeline._record_progress(
            codebase_id,
            root,
            phase=phase,
            current=current,
            total=total,
            percentage=percentage,
            progress_callback=progress_callback,
        )

    def _dedupe_results(self, results: list[CodeSearchResult]) -> list[CodeSearchResult]:
        return self.search_service.dedupe_results(results)
