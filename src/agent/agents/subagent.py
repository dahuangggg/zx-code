"""agents.subagent — 子代理运行器（s04）。

``SubagentRunner`` 在独立的消息历史（空 messages 列表）中运行嵌套 agent，
核心设计思路：上下文隔离 + 共享工具注册表。

关键特性：
  depth 限制   — subagent_max_depth 防止递归子代理无限嵌套（默认 1 层）
  session_id   — 子代理使用 "<parent>:subagent:<label>:<uuid>" 格式，历史独立存储
  LaneScheduler— 如提供，子代理通过 "subagent" 泳道排队，不与主对话抢占 LLM

``spawn_background()`` 将子代理包装为 asyncio.create_task，
立即返回 Queue，主循环可 await queue.get() 异步获取结果。
"""

from __future__ import annotations


import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from agent.scheduling.lanes import LaneScheduler


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

    def spawn_background(
        self,
        task: str,
        *,
        label: str = "worker",
    ) -> asyncio.Queue[SubagentRunResult]:
        result_queue: asyncio.Queue[SubagentRunResult] = asyncio.Queue()

        async def _run() -> None:
            result = await self.run(task, label=label)
            await result_queue.put(result)

        asyncio.create_task(_run())
        return result_queue


def _safe_label(label: str) -> str:
    normalized = label.strip() or "worker"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized)
    return safe.strip("._") or "worker"
