from __future__ import annotations

import asyncio

from agent.lanes import LaneScheduler


async def test_lane_scheduler_runs_high_priority_jobs_first_when_waiting() -> None:
    scheduler = LaneScheduler(autostart=False)
    order: list[str] = []

    async def record(name: str) -> str:
        order.append(name)
        return name

    heartbeat = scheduler.submit("heartbeat", lambda: record("heartbeat"))
    cron = scheduler.submit("cron", lambda: record("cron"))
    main = scheduler.submit("main", lambda: record("main"))

    scheduler.start()

    assert await main == "main"
    assert await cron == "cron"
    assert await heartbeat == "heartbeat"
    assert order == ["main", "cron", "heartbeat"]
    assert [record.lane for record in scheduler.history] == ["main", "cron", "heartbeat"]

    await scheduler.close()


async def test_lane_scheduler_does_not_preempt_running_lower_priority_job() -> None:
    scheduler = LaneScheduler()
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def heartbeat_job() -> str:
        order.append("heartbeat-start")
        started.set()
        await release.wait()
        order.append("heartbeat-end")
        return "heartbeat"

    async def main_job() -> str:
        order.append("main")
        return "main"

    heartbeat = scheduler.submit("heartbeat", heartbeat_job)
    await started.wait()
    main = scheduler.submit("main", main_job)
    await asyncio.sleep(0)

    assert order == ["heartbeat-start"]

    release.set()

    assert await heartbeat == "heartbeat"
    assert await main == "main"
    assert order == ["heartbeat-start", "heartbeat-end", "main"]

    await scheduler.close()
