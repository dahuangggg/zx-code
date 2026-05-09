"""agents.worktree — Git worktree 隔离（s18）。

``WorktreeManager`` 为每个子代理任务创建一个独立的 Git worktree，
实现文件系统级别的隔离，防止并发代理互相覆盖文件。

工作流程：
  1. ``create()`` — git worktree add，在指定目录创建独立的工作副本，切到新分支
  2. 子代理在该 worktree 路径下操作文件
  3. ``cleanup()`` — git worktree remove，清除工作副本（可选保留分支供合并）

前提条件：项目必须是 git 仓库（非 git 目录会抛出异常）。
"""

from __future__ import annotations


import re
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class WorktreeLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    branch: str
    path: Path


class WorktreeManager:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        worktree_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.worktree_root = (
            Path(worktree_root).expanduser().resolve()
            if worktree_root is not None
            else self.repo_root / ".agent" / "worktrees"
        )

    def create(self, task_id: str, *, base_ref: str = "HEAD") -> WorktreeLease:
        self._ensure_git_repo()
        safe_id = _safe_task_id(task_id)
        suffix = uuid.uuid4().hex[:8]
        branch = f"zx/{safe_id}-{suffix}"
        path = self.worktree_root / f"{safe_id}-{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)

        self._git("worktree", "add", "-b", branch, str(path), base_ref)
        return WorktreeLease(task_id=task_id, branch=branch, path=path)

    def cleanup(self, lease: WorktreeLease, *, delete_branch: bool = False) -> None:
        if lease.path.exists():
            self._git("worktree", "remove", "--force", str(lease.path))
        if delete_branch:
            result = self._git(
                "branch",
                "--list",
                lease.branch,
                capture=True,
                check=False,
            )
            if result.strip():
                self._git("branch", "-D", lease.branch)

    def _ensure_git_repo(self) -> None:
        if not self.repo_root.exists():
            raise RuntimeError(f"not a git repository: {self.repo_root}")
        result = self._git(
            "rev-parse",
            "--show-toplevel",
            capture=True,
            check=False,
        )
        if not result:
            raise RuntimeError(f"not a git repository: {self.repo_root}")

    def _git(
        self,
        *args: str,
        capture: bool = False,
        check: bool = True,
    ) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        if result.returncode != 0:
            return ""
        return result.stdout.strip() if capture else result.stdout.strip()


def _safe_task_id(task_id: str) -> str:
    normalized = task_id.strip().lower() or "task"
    safe = re.sub(r"[^a-z0-9_.-]+", "-", normalized)
    return safe.strip(".-_/") or "task"
