"""CodeContext 索引的 snapshot 和 status 持久化。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CodebaseSnapshotStore:
    """管理 CodeContext snapshot/status 路径和 JSON 持久化。"""

    def __init__(self, snapshot_dir: str | Path) -> None:
        self.snapshot_dir = Path(snapshot_dir).expanduser()

    def snapshot_path(self, codebase_id: str) -> Path:
        return self.snapshot_dir / f"{codebase_id}.json"

    def status_path(self, codebase_id: str) -> Path:
        return self.snapshot_dir / f"{codebase_id}.status.json"

    def lock_path(self, codebase_id: str) -> Path:
        return self.snapshot_dir / "locks" / f"{codebase_id}.lock"

    def load_snapshot(self, codebase_id: str) -> dict[str, Any]:
        path = self.snapshot_path(codebase_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def load_status(self, codebase_id: str) -> dict[str, Any]:
        path = self.status_path(codebase_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_snapshot(self, codebase_id: str, snapshot: dict[str, Any]) -> None:
        self.write_json(self.snapshot_path(codebase_id), codebase_id, snapshot)

    def write_status(self, codebase_id: str, status: dict[str, Any]) -> None:
        self.write_json(self.status_path(codebase_id), codebase_id, status)

    def delete_snapshot(self, codebase_id: str) -> None:
        path = self.snapshot_path(codebase_id)
        if path.exists():
            path.unlink()

    def delete_status(self, codebase_id: str) -> None:
        path = self.status_path(codebase_id)
        if path.exists():
            path.unlink()

    def iter_snapshot_paths(self) -> list[Path]:
        if not self.snapshot_dir.exists():
            return []
        return [
            path
            for path in sorted(self.snapshot_dir.glob("*.json"))
            if not path.name.endswith(".status.json")
        ]

    def write_json(self, path: Path, codebase_id: str, payload: dict[str, Any]) -> None:
        """原子写入 snapshot/status JSON，避免状态文件损坏。"""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            dir=self.snapshot_dir,
            prefix=f".tmp.{codebase_id}.",
            suffix=".json",
        )
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
