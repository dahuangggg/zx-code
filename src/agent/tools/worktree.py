from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.tools.base import Tool
from agent.agents.worktree import WorktreeLease, WorktreeManager


class WorktreeCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    base_ref: str = "HEAD"


class WorktreeCleanupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    branch: str
    path: str
    delete_branch: bool = False


class WorktreeCreateTool(Tool):
    name = "worktree_create"
    description = "Create an isolated git worktree for a focused coding task."
    input_model = WorktreeCreateInput

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    async def run(self, arguments: WorktreeCreateInput) -> dict[str, object]:
        lease = self.manager.create(arguments.task_id, base_ref=arguments.base_ref)
        return lease.model_dump(mode="json")


class WorktreeCleanupTool(Tool):
    name = "worktree_cleanup"
    description = "Remove an isolated git worktree and optionally delete its branch."
    input_model = WorktreeCleanupInput

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    async def run(self, arguments: WorktreeCleanupInput) -> dict[str, object]:
        lease = WorktreeLease(
            task_id=arguments.task_id,
            branch=arguments.branch,
            path=Path(arguments.path),
        )
        self.manager.cleanup(lease, delete_branch=arguments.delete_branch)
        return {
            "task_id": lease.task_id,
            "branch": lease.branch,
            "path": str(lease.path),
            "removed": True,
            "delete_branch": arguments.delete_branch,
        }
