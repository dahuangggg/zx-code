from __future__ import annotations

from pathlib import Path

import pytest

from agent.operations import CheckpointConflict, FileCheckpointStore


def test_checkpoint_undo_restores_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)

    store.begin("op-1", tool_name="write_file", arguments={"path": str(target)})
    target.write_text("after\n", encoding="utf-8")
    evidence = store.finalize("op-1")

    assert evidence["pre_sha256"] != evidence["post_sha256"]
    assert Path(evidence["patch_path"]).read_text(encoding="utf-8").startswith("---")

    restored = store.undo("op-1")

    assert restored == target.resolve()
    assert target.read_text(encoding="utf-8") == "before\n"
    assert store.get("op-1").status == "undone"


def test_checkpoint_undo_deletes_file_created_by_operation(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)
    store.begin("op-new", tool_name="write_file", arguments={"path": "new.txt"})
    target.write_text("created\n", encoding="utf-8")
    store.finalize("op-new")

    store.undo("op-new")

    assert not target.exists()


def test_checkpoint_undo_refuses_to_overwrite_later_user_change(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)
    store.begin("op-1", tool_name="edit_file", arguments={"path": str(target)})
    target.write_text("agent change\n", encoding="utf-8")
    store.finalize("op-1")
    target.write_text("user change\n", encoding="utf-8")

    with pytest.raises(CheckpointConflict):
        store.undo("op-1")

    assert target.read_text(encoding="utf-8") == "user change\n"


def test_prepared_checkpoint_cannot_be_automatically_undone(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)
    store.begin("op-unknown", tool_name="write_file", arguments={"path": str(target)})
    target.write_text("possibly partial\n", encoding="utf-8")

    with pytest.raises(CheckpointConflict, match="not finalized"):
        store.undo("op-unknown")


def test_latest_finalized_returns_most_recent_undoable_checkpoint(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before first\n", encoding="utf-8")
    second.write_text("before second\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)

    store.begin("op-first", tool_name="edit_file", arguments={"path": str(first)})
    first.write_text("after first\n", encoding="utf-8")
    store.finalize("op-first")
    store.begin("op-second", tool_name="edit_file", arguments={"path": str(second)})
    second.write_text("after second\n", encoding="utf-8")
    store.finalize("op-second")

    assert store.latest_finalized().operation_id == "op-second"

    store.undo("op-second")

    assert store.latest_finalized().operation_id == "op-first"


def test_latest_finalized_fails_when_no_file_change_can_be_undone(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)

    with pytest.raises(KeyError, match="no undoable file operation"):
        store.latest_finalized()
