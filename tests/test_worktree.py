from __future__ import annotations

import subprocess
import json
from pathlib import Path

from agent.agents.worktree import WorktreeManager
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
        manager.cleanup(lease, delete_branch=True)

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
    worktree_path = Path(payload["path"])

    assert not created.is_error
    assert worktree_path.exists()

    cleaned = await registry.execute(
        "worktree_cleanup",
        {
            "task_id": payload["task_id"],
            "branch": payload["branch"],
            "path": payload["path"],
            "delete_branch": True,
        },
        call_id="wt-2",
    )

    assert not cleaned.is_error
    assert not worktree_path.exists()
