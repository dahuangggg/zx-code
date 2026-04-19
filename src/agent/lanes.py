from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


LaneName = Literal["main", "subagent", "cron", "heartbeat"]

LANE_PRIORITIES: dict[str, int] = {
    "main": 0,
    "subagent": 10,
    "cron": 20,
    "heartbeat": 30,
}

_current_scheduler: ContextVar["LaneScheduler | None"] = ContextVar(
    "current_lane_scheduler",
    default=None,
)


class LaneRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    lane: str
    priority: int
    wait_ms: float
    duration_ms: float
    status: Literal["succeeded", "failed"]
    error: str = ""


@dataclass
class _LaneJob:
    lane: str
    priority: int
    job_id: str
    submitted_at: float
    run: Callable[[], Awaitable[Any]]
    future: asyncio.Future[Any]


@dataclass(order=True)
class _QueuedLaneJob:
    priority: int
    sequence: int
    job: _LaneJob = field(compare=False)


class LaneScheduler:
    """Cooperative priority scheduler for agent turns.

    Lower priority numbers run first. Running jobs are not preempted; a newly
    arrived high-priority job waits until the current LLM/tool turn finishes.
    """

    def __init__(self, *, autostart: bool = True) -> None:
        self.autostart = autostart
        self.history: list[LaneRunRecord] = []
        self._queue: asyncio.PriorityQueue[_QueuedLaneJob] = asyncio.PriorityQueue()
        self._sequence = 0
        self._worker_task: asyncio.Task[None] | None = None

    def submit(
        self,
        lane: LaneName | str,
        run: Callable[[], Awaitable[Any]],
        *,
        job_id: str | None = None,
    ) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        priority = LANE_PRIORITIES.get(lane, 100)
        self._sequence += 1
        self._queue.put_nowait(
            _QueuedLaneJob(
                priority=priority,
                sequence=self._sequence,
                job=_LaneJob(
                    lane=lane,
                    priority=priority,
                    job_id=job_id or uuid.uuid4().hex[:12],
                    submitted_at=time.monotonic(),
                    run=run,
                    future=future,
                ),
            )
        )
        if self.autostart:
            self.start()
        return future

    async def run(
        self,
        lane: LaneName | str,
        run: Callable[[], Awaitable[Any]],
        *,
        job_id: str | None = None,
    ) -> Any:
        if _current_scheduler.get() is self:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Any] = loop.create_future()
            job = _LaneJob(
                lane=lane,
                priority=LANE_PRIORITIES.get(lane, 100),
                job_id=job_id or uuid.uuid4().hex[:12],
                submitted_at=time.monotonic(),
                run=run,
                future=future,
            )
            await self._execute_job(job)
            return await future
        return await self.submit(lane, run, job_id=job_id)

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def close(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    async def _worker(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                await self._execute_job(queued.job)
            finally:
                self._queue.task_done()

    async def _execute_job(self, job: _LaneJob) -> None:
        started_at = time.monotonic()
        status: Literal["succeeded", "failed"] = "succeeded"
        error = ""
        token = _current_scheduler.set(self)
        try:
            result = await job.run()
        except Exception as exc:
            status = "failed"
            error = str(exc)
            if not job.future.cancelled():
                job.future.set_exception(exc)
        else:
            if not job.future.cancelled():
                job.future.set_result(result)
        finally:
            _current_scheduler.reset(token)
            finished_at = time.monotonic()
            self.history.append(
                LaneRunRecord(
                    job_id=job.job_id,
                    lane=job.lane,
                    priority=job.priority,
                    wait_ms=(started_at - job.submitted_at) * 1000,
                    duration_ms=(finished_at - started_at) * 1000,
                    status=status,
                    error=error,
                )
            )
