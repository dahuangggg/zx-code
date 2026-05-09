from __future__ import annotations

from pathlib import Path

from agent.state.tasks import TaskStore


def test_task_store_persists_dag_and_unlocks_dependents(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / ".tasks")
    parent = store.create("write implementation")
    child = store.create("run verification", blocked_by=[parent.id])

    assert child.status == "blocked"
    assert store.ready() == [parent]

    completed, unlocked = store.complete(parent.id)
    reloaded_child = store.get(child.id)

    assert completed.status == "completed"
    assert [task.id for task in unlocked] == [child.id]
    assert reloaded_child.status == "pending"
    assert (tmp_path / ".tasks" / f"{parent.id}.json").exists()


def test_task_store_creates_ready_task_when_blockers_are_complete(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / ".tasks")
    parent = store.create("write implementation")
    store.complete(parent.id)

    child = store.create("run verification", blocked_by=[parent.id])

    assert child.status == "pending"
    assert store.get(child.id).status == "pending"
    assert [task.id for task in store.ready()] == [child.id]


def test_task_store_rejects_missing_blockers(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / ".tasks")

    try:
        store.create("blocked task", blocked_by=["missing"])
    except KeyError as exc:
        assert "missing blocker" in str(exc)
    else:
        raise AssertionError("expected missing blocker to be rejected")
