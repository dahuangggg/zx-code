"""agent 任务的 Git worktree 隔离。

``WorktreeManager`` 为任务创建独立 Git worktree 和分支，并在调用方完成后清理
文件系统租约。本模块只封装 Git worktree 操作，合并策略由外层决定。
"""

from __future__ import annotations


import re
import subprocess
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class WorktreeLease(BaseModel):
    """一次 worktree 创建后的租约信息。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    branch: str
    path: Path


class WorktreeManager:
    """封装 git worktree add/remove 的管理器。"""

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
            else self.repo_root / ".zx-code" / "state" / "worktrees"
        )

    def create(self, task_id: str, *, base_ref: str = "HEAD") -> WorktreeLease:
        """基于 base_ref 创建新分支和隔离 worktree。"""
        self._ensure_git_repo()
        safe_id = _safe_task_id(task_id)
        suffix = uuid.uuid4().hex[:8]
        branch = f"zx/{safe_id}-{suffix}"
        path = self.worktree_root / f"{safe_id}-{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)

        self._git("worktree", "add", "-b", branch, str(path), base_ref)
        return WorktreeLease(task_id=task_id, branch=branch, path=path)

    def cleanup(self, lease: WorktreeLease, *, delete_branch: bool = False) -> None:
        """删除 worktree；可选删除对应分支。"""
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
        """确认 repo_root 是有效 git 仓库。"""
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
        """运行 git 子命令，并按 check 决定是否抛错。"""
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
    """把 task id 转成分支名和目录名可用片段。"""
    normalized = task_id.strip().lower() or "task"
    safe = re.sub(r"[^a-z0-9_.-]+", "-", normalized)
    return safe.strip(".-_/") or "task"
