"""子代理运行器，负责隔离的嵌套 agent 执行。

``SubagentRunner`` 为每个嵌套任务创建独立 session_id，同时复用父运行时的工具注册表。
``LaneScheduler`` 可用于串行化子代理工作，让本模块只关注嵌套 agent 执行。
"""

from __future__ import annotations


import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from agent.core.concurrency import LaneScheduler


SubagentTurnHandler = Callable[[str, str, int], Awaitable[str]]


class SubagentRecursionError(RuntimeError):
    """子代理嵌套超过 max_depth。"""

    pass


class SubagentRunResult(BaseModel):
    """子代理一次运行的最终摘要。"""

    model_config = ConfigDict(extra="forbid")

    task: str
    label: str
    session_id: str
    final_text: str
    depth: int


class SubagentRunner:
    """创建隔离 session 并运行子 agent 的协调器。"""

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
        """同步等待子代理完成并返回结果。"""
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

    def spawn_background(
        self,
        task: str,
        *,
        label: str = "worker",
    ) -> asyncio.Queue[SubagentRunResult]:
        """后台启动子代理，并用 Queue 返回最终结果。"""
        result_queue: asyncio.Queue[SubagentRunResult] = asyncio.Queue()

        async def _run() -> None:
            result = await self.run(task, label=label)
            await result_queue.put(result)

        asyncio.create_task(_run())
        return result_queue


def _safe_label(label: str) -> str:
    """把子代理 label 转成 session_id 可用片段。"""
    normalized = label.strip() or "worker"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized)
    return safe.strip("._") or "worker"
