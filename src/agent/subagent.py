from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from agent.lanes import LaneScheduler


SubagentTurnHandler = Callable[[str, str, int], Awaitable[str]]


class SubagentRecursionError(RuntimeError):
    pass


class SubagentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    label: str
    session_id: str
    final_text: str
    depth: int


class SubagentRunner:
    def __init__(
        self,
        *,
        run_agent_text: SubagentTurnHandler,
        parent_session_id: str,
        lane_scheduler: LaneScheduler | None = None,
        max_depth: int = 1,
        current_depth: int = 0,
    ) -> None:
        self.run_agent_text = run_agent_text
        self.parent_session_id = parent_session_id
        self.lane_scheduler = lane_scheduler
        self.max_depth = max_depth
        self.current_depth = current_depth

    async def run(self, task: str, *, label: str = "worker") -> SubagentRunResult:
        if self.current_depth >= self.max_depth:
            raise SubagentRecursionError(
                f"subagent recursion limit reached: {self.current_depth}/{self.max_depth}"
            )
        clean_label = _safe_label(label)
        session_id = f"{self.parent_session_id}:subagent:{clean_label}:{uuid.uuid4().hex[:8]}"
        next_depth = self.current_depth + 1

        async def execute() -> str:
            return await self.run_agent_text(task, session_id, next_depth)

        if self.lane_scheduler is None:
            final_text = await execute()
        else:
            final_text = await self.lane_scheduler.run(
                "subagent",
                execute,
                job_id=session_id,
            )
        return SubagentRunResult(
            task=task,
            label=clean_label,
            session_id=session_id,
            final_text=final_text,
            depth=next_depth,
        )


def _safe_label(label: str) -> str:
    normalized = label.strip() or "worker"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized)
    return safe.strip("._") or "worker"
