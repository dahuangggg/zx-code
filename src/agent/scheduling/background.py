"""scheduling.background — asyncio 后台任务管理器（s13）。

``BackgroundTaskManager`` 封装了 asyncio.create_task 的常见模式：
  1. 按 task_id 启动任务，防止同名任务重复运行
  2. 任务完成后将结果（成功或失败）放入 asyncio.Queue
  3. ``cancel_all()`` 优雅取消所有仍在运行的任务

使用场景：
  - channel loop 中周期性触发心跳 tick 和 cron tick
  - 将耗时的 agent 任务移至后台，主循环继续接收消息
"""

from __future__ import annotations


import asyncio
from collections.abc import Awaitable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


BackgroundStatus = Literal["succeeded", "failed"]


class BackgroundResult(BaseModel):
    """后台任务完成后放入 results 队列的结果。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: BackgroundStatus
    result: Any = None
    error: str = ""


class BackgroundTaskManager:
    """带结果队列的小型 asyncio 任务管理器。"""

    def __init__(self) -> None:
        self.results: asyncio.Queue[BackgroundResult] = asyncio.Queue()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, task_id: str, awaitable: Awaitable[Any]) -> asyncio.Task[None]:
        """启动后台任务；同 task_id 仍在运行时拒绝重复启动。"""
        if task_id in self._tasks and not self._tasks[task_id].done():
            raise ValueError(f"background task already running: {task_id}")
        task = asyncio.create_task(self._run(task_id, awaitable))
        self._tasks[task_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(task_id, None))
        return task

    async def next_result(self) -> BackgroundResult:
        """等待下一个后台任务结果。"""
        return await self.results.get()

    async def cancel_all(self) -> None:
        """取消所有仍在运行的后台任务。"""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, task_id: str, awaitable: Awaitable[Any]) -> None:
        try:
            result = await awaitable
        except Exception as exc:
            await self.results.put(
                BackgroundResult(
                    task_id=task_id,
                    status="failed",
                    error=str(exc),
                )
            )
            return
        await self.results.put(
            BackgroundResult(
                task_id=task_id,
                status="succeeded",
                result=result,
            )
        )
