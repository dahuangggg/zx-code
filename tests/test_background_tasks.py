from __future__ import annotations

import pytest

from agent.scheduling.background import BackgroundTaskManager


@pytest.mark.asyncio
async def test_background_task_manager_reports_success() -> None:
    manager = BackgroundTaskManager()

    async def work() -> str:
        return "ok"

    manager.start("job-1", work())
    result = await manager.next_result()

    assert result.task_id == "job-1"
    assert result.status == "succeeded"
    assert result.result == "ok"


@pytest.mark.asyncio
async def test_background_task_manager_reports_failure() -> None:
    manager = BackgroundTaskManager()

    async def work() -> str:
        raise RuntimeError("boom")

    manager.start("job-2", work())
    result = await manager.next_result()

    assert result.task_id == "job-2"
    assert result.status == "failed"
    assert "boom" in result.error
