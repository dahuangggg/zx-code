"""write/edit operation 的可验证文件 checkpoint 与安全 undo。"""

from __future__ import annotations

import base64
import difflib
import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class CheckpointConflict(RuntimeError):
    pass


class FileCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    tool_name: Literal["write_file", "edit_file"]
    path: str
    status: Literal["prepared", "finalized", "undone"]
    existed: bool
    preimage_b64: str
    pre_sha256: str | None
    post_sha256: str | None = None
    mode: int | None = None
    patch_path: str | None = None
    created_at: datetime


class FileCheckpointStore:
    def __init__(self, root: str | Path, *, project_root: str | Path) -> None:
        self.root = Path(root)
        self.project_root = Path(project_root).expanduser().resolve()

    def begin(
        self,
        operation_id: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> FileCheckpoint | None:
        if tool_name not in {"write_file", "edit_file"}:
            return None
        path_value = arguments.get("path")
        if not path_value:
            return None
        path = Path(str(path_value)).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve(strict=False)
        if path.exists() and not path.is_file():
            raise CheckpointConflict(f"checkpoint target is not a file: {path}")
        existed = path.exists()
        preimage = path.read_bytes() if existed else b""
        mode = path.stat().st_mode & 0o777 if existed else None
        checkpoint = FileCheckpoint(
            operation_id=_safe_operation_id(operation_id),
            tool_name=tool_name,
            path=str(path),
            status="prepared",
            existed=existed,
            preimage_b64=base64.b64encode(preimage).decode("ascii"),
            pre_sha256=_sha256(preimage) if existed else None,
            mode=mode,
            created_at=datetime.now(UTC),
        )
        self._write(checkpoint)
        return checkpoint

    def finalize(self, operation_id: str) -> dict[str, Any]:
        checkpoint = self.get(operation_id)
        if checkpoint.status != "prepared":
            raise CheckpointConflict(
                f"checkpoint is already {checkpoint.status}: {operation_id}"
            )
        path = Path(checkpoint.path)
        if not path.is_file():
            raise CheckpointConflict(f"operation did not leave a file to checkpoint: {path}")
        postimage = path.read_bytes()
        patch_path = self.root / f"{_safe_operation_id(operation_id)}.patch"
        _write_private_bytes(
            patch_path,
            _unified_diff(
                base64.b64decode(checkpoint.preimage_b64),
                postimage,
                path,
            ).encode("utf-8"),
        )
        finalized = checkpoint.model_copy(
            update={
                "status": "finalized",
                "post_sha256": _sha256(postimage),
                "patch_path": str(patch_path),
            }
        )
        self._write(finalized)
        return {
            "checkpoint_path": str(self._path(operation_id)),
            "patch_path": str(patch_path),
            "file_path": str(path),
            "pre_sha256": finalized.pre_sha256,
            "post_sha256": finalized.post_sha256,
        }

    def undo(self, operation_id: str) -> Path:
        checkpoint = self.get(operation_id)
        if checkpoint.status != "finalized":
            raise CheckpointConflict(
                f"checkpoint is not finalized: {operation_id} ({checkpoint.status})"
            )
        path = Path(checkpoint.path)
        current_hash = _sha256(path.read_bytes()) if path.is_file() else None
        if current_hash != checkpoint.post_sha256:
            raise CheckpointConflict(
                "current file differs from the recorded postimage; refusing undo"
            )
        if checkpoint.existed:
            preimage = base64.b64decode(checkpoint.preimage_b64)
            _write_private_bytes(path, preimage, mode=checkpoint.mode)
        elif path.exists():
            path.unlink()
        self._write(checkpoint.model_copy(update={"status": "undone"}))
        return path

    def get(self, operation_id: str) -> FileCheckpoint:
        path = self._path(operation_id)
        if not path.exists():
            raise KeyError(f"unknown checkpoint: {operation_id}")
        return FileCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def latest_finalized(self) -> FileCheckpoint:
        """返回最近一次仍可撤销的文件修改。"""
        candidates: list[FileCheckpoint] = []
        for path in self.root.glob("*.json"):
            checkpoint = FileCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if checkpoint.status == "finalized":
                candidates.append(checkpoint)
        if not candidates:
            raise KeyError("no undoable file operation")
        return max(
            candidates,
            key=lambda checkpoint: (checkpoint.created_at, checkpoint.operation_id),
        )

    def _path(self, operation_id: str) -> Path:
        return self.root / f"{_safe_operation_id(operation_id)}.json"

    def _write(self, checkpoint: FileCheckpoint) -> None:
        _write_private_bytes(
            self._path(checkpoint.operation_id),
            checkpoint.model_dump_json(indent=2).encode("utf-8"),
        )


def _safe_operation_id(operation_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", operation_id):
        raise ValueError("invalid operation id")
    return operation_id


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _unified_diff(before: bytes, after: bytes, path: Path) -> str:
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        return f"Binary file changed: {path}\n"
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )


def _write_private_bytes(path: Path, content: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode if mode is not None else 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise
