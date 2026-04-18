from __future__ import annotations

import json
from datetime import UTC, datetime

import agent.cron as cron_module
from agent.channels.base import InboundMessage
from agent.cron import CronJob, CronScheduler
from agent.delivery import DeliveryQueue
from agent.heartbeat import ActivityTracker, HeartbeatConfig, HeartbeatRunner


async def test_heartbeat_skips_when_user_lane_is_busy(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    activity = ActivityTracker()
    inbound = InboundMessage(
        text="hello",
        sender_id="u",
        channel="telegram",
        account_id="bot",
        peer_id="peer",
    )
    activity.mark_inbound(inbound, now=100)
    calls: list[str] = []

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        calls.append(prompt)
        return "should not run"

    runner = HeartbeatRunner(
        config=HeartbeatConfig(
            enabled=True,
            interval_s=10,
            min_idle_s=60,
            channel="telegram",
            account_id="bot",
            to="peer",
            prompt="heartbeat",
        ),
        delivery_queue=queue,
        run_agent_turn=run_agent_turn,
        activity_tracker=activity,
    )

    result = await runner.tick(now=120)

    assert result is None
    assert calls == []
    assert queue.ready(now=120) == []


async def test_heartbeat_enqueues_non_sentinel_output(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return "user-facing update"

    runner = HeartbeatRunner(
        config=HeartbeatConfig(
            enabled=True,
            interval_s=10,
            channel="telegram",
            account_id="bot",
            to="peer",
            prompt="heartbeat",
        ),
        delivery_queue=queue,
        run_agent_turn=run_agent_turn,
    )

    entry = await runner.tick(now=100)

    assert entry is not None
    assert entry.channel == "telegram"
    assert entry.to == "peer"
    assert entry.text == "user-facing update"
    assert queue.ready(now=100)[0].metadata["source"] == "heartbeat"


async def test_heartbeat_ignores_sentinel(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return "HEARTBEAT_OK"

    runner = HeartbeatRunner(
        config=HeartbeatConfig(enabled=True, channel="telegram", to="peer"),
        delivery_queue=queue,
        run_agent_turn=run_agent_turn,
    )

    assert await runner.tick(now=100) is None
    assert queue.ready(now=100) == []


async def test_cron_every_job_enqueues_delivery(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    calls: list[tuple[str, str]] = []

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        calls.append((prompt, session_id))
        return "cron result"

    scheduler = CronScheduler(delivery_queue=queue, run_agent_turn=run_agent_turn)
    scheduler.add_every(
        job_id="job-1",
        interval_s=10,
        prompt="run cron",
        channel="telegram",
        to="peer",
        now=100,
    )

    assert await scheduler.tick(now=109) == []
    entries = await scheduler.tick(now=110)

    assert len(entries) == 1
    assert entries[0].text == "cron result"
    assert calls == [("run cron", "cron:job-1")]


async def test_cron_at_job_runs_once(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    calls = 0

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        nonlocal calls
        calls += 1
        return "at result"

    scheduler = CronScheduler(delivery_queue=queue, run_agent_turn=run_agent_turn)
    scheduler.add_at(
        job_id="job-at",
        when=100,
        prompt="run at",
        channel="telegram",
        to="peer",
    )

    first = await scheduler.tick(now=100)
    second = await scheduler.tick(now=101)

    assert len(first) == 1
    assert second == []
    assert calls == 1


async def test_cron_file_loads_jobs(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path / "delivery", jitter_s=0)
    path = tmp_path / "cron.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "daily",
                        "kind": "cron",
                        "schedule": "0 8 * * *",
                        "prompt": "daily summary",
                        "channel": "telegram",
                        "to": "peer",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return "daily result"

    scheduler = CronScheduler.from_file(
        path,
        delivery_queue=queue,
        run_agent_turn=run_agent_turn,
    )
    now = datetime(2026, 4, 18, 8, 0, tzinfo=UTC).timestamp()
    entries = await scheduler.tick(now=now)

    assert len(scheduler.jobs) == 1
    assert scheduler.jobs[0].id == "daily"
    assert len(entries) == 1


async def test_cron_file_at_job_uses_schedule(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path / "delivery", jitter_s=0)
    path = tmp_path / "cron.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "once",
                    "kind": "at",
                    "schedule": "100",
                    "prompt": "once",
                    "channel": "telegram",
                    "to": "peer",
                }
            ]
        ),
        encoding="utf-8",
    )

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return "once result"

    scheduler = CronScheduler.from_file(
        path,
        delivery_queue=queue,
        run_agent_turn=run_agent_turn,
    )

    assert await scheduler.tick(now=99) == []
    assert len(await scheduler.tick(now=100)) == 1


def test_simple_cron_job_model() -> None:
    job = CronJob(
        id="demo",
        kind="cron",
        schedule="*/5 * * * *",
        prompt="demo",
        channel="telegram",
        to="peer",
    )

    assert job.enabled


async def test_cron_simple_parser_fallback_when_croniter_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cron_module, "croniter", None)
    queue = DeliveryQueue(tmp_path / "delivery", jitter_s=0)

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return "fallback cron result"

    scheduler = CronScheduler(delivery_queue=queue, run_agent_turn=run_agent_turn)
    scheduler.add_cron(
        job_id="fallback",
        cron_expr="*/5 * * * *",
        prompt="fallback",
        channel="telegram",
        to="peer",
    )

    not_due = datetime(2026, 4, 18, 8, 4, tzinfo=UTC).timestamp()
    due = datetime(2026, 4, 18, 8, 5, tzinfo=UTC).timestamp()

    assert await scheduler.tick(now=not_due) == []
    entries = await scheduler.tick(now=due)

    assert len(entries) == 1
    assert entries[0].text == "fallback cron result"
