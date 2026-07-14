from __future__ import annotations

import subprocess
import json
from pathlib import Path

from agent.agents.worktree import WorktreeLease, WorktreeManager
from agent.tools import build_default_registry


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def test_worktree_manager_creates_isolated_worktree_and_cleans_up(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "answer.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "answer.txt")
    _git(repo, "commit", "-m", "init")

    manager = WorktreeManager(
        repo_root=repo,
        worktree_root=tmp_path / "worktrees",
    )
    lease = manager.create("fix answer")

    try:
        assert lease.path.exists()
        assert lease.branch.startswith("zx/fix-answer-")
        assert _git(lease.path, "branch", "--show-current") == lease.branch

        (lease.path / "answer.txt").write_text("child\n", encoding="utf-8")

        assert (repo / "answer.txt").read_text(encoding="utf-8") == "main\n"
        assert (lease.path / "answer.txt").read_text(encoding="utf-8") == "child\n"
    finally:
        manager.cleanup(lease.lease_id, delete_branch=True)

    assert not lease.path.exists()
    assert lease.branch not in _git(repo, "branch", "--list", lease.branch)


def test_worktree_manager_rejects_non_git_repo(tmp_path: Path) -> None:
    manager = WorktreeManager(repo_root=tmp_path / "not-git")

    try:
        manager.create("task")
    except RuntimeError as exc:
        assert "not a git repository" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


async def test_worktree_tools_create_and_cleanup_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "answer.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "answer.txt")
    _git(repo, "commit", "-m", "init")

    registry = build_default_registry(
        worktree_manager=WorktreeManager(
            repo_root=repo,
            worktree_root=tmp_path / "worktrees",
        )
    )

    created = await registry.execute(
        "worktree_create",
        {"task_id": "tool task"},
        call_id="wt-1",
    )
    payload = json.loads(created.content)
    data = payload["data"]
    worktree_path = Path(data["path"])

    assert not created.is_error
    assert worktree_path.exists()

    cleaned = await registry.execute(
        "worktree_cleanup",
        {
            "lease_id": data["lease_id"],
            "delete_branch": True,
        },
        call_id="wt-2",
    )

    assert not cleaned.is_error
    assert not worktree_path.exists()


def test_worktree_manager_rejects_unknown_lease_before_running_git(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = WorktreeManager(
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "worktrees",
    )
    git_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal git_called
        git_called = True
        raise AssertionError("unknown lease must be rejected before git")

    monkeypatch.setattr(manager, "_git", fail_if_called)

    try:
        manager.cleanup("forged-lease", delete_branch=True)
    except ValueError as exc:
        assert "unknown worktree lease" in str(exc)
    else:
        raise AssertionError("expected forged lease to be rejected")

    assert git_called is False


def test_worktree_manager_rejects_lease_pointing_at_main_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manager = WorktreeManager(
        repo_root=repo,
        worktree_root=tmp_path / "worktrees",
    )
    manager._leases["forged-main"] = WorktreeLease(
        lease_id="forged-main",
        task_id="forged",
        branch="main",
        path=repo,
    )

    try:
        manager.cleanup("forged-main", delete_branch=True)
    except ValueError as exc:
        assert "outside managed root" in str(exc)
    else:
        raise AssertionError("expected main worktree lease to be rejected")


async def test_worktree_cleanup_rejects_model_supplied_branch_and_path(
    tmp_path: Path,
) -> None:
    manager = WorktreeManager(
        repo_root=tmp_path / "repo",
        worktree_root=tmp_path / "worktrees",
    )
    registry = build_default_registry(worktree_manager=manager)

    result = await registry.execute(
        "worktree_cleanup",
        {
            "lease_id": "forged-lease",
            "branch": "main",
            "path": str(tmp_path),
            "delete_branch": True,
        },
        call_id="wt-forged",
    )

    assert result.is_error
    assert result.envelope.error is not None
    assert result.envelope.error.code == "INVALID_PARAM"
