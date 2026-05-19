from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.agents.worktree import WorktreeLease, WorktreeManager
from agent.models import ToolEnvelope
from agent.tools.base import Tool


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

    async def run(self, arguments: WorktreeCreateInput) -> ToolEnvelope:
        lease = self.manager.create(arguments.task_id, base_ref=arguments.base_ref)
        data = lease.model_dump(mode="json")
        return self.success(data=data, text=f"Created worktree for task '{arguments.task_id}' at {data.get('path', '?')} (branch: {data.get('branch', '?')}).")


class WorktreeCleanupTool(Tool):
    name = "worktree_cleanup"
    description = "Remove an isolated git worktree and optionally delete its branch."
    input_model = WorktreeCleanupInput

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    async def run(self, arguments: WorktreeCleanupInput) -> ToolEnvelope:
        lease = WorktreeLease(
            task_id=arguments.task_id,
            branch=arguments.branch,
            path=Path(arguments.path),
        )
        self.manager.cleanup(lease, delete_branch=arguments.delete_branch)
        return self.success(
            data={"task_id": lease.task_id, "branch": lease.branch, "path": str(lease.path), "removed": True},
            text=f"Cleaned up worktree for task '{arguments.task_id}' (branch '{arguments.branch}' {'deleted' if arguments.delete_branch else 'kept'}).",
        )
