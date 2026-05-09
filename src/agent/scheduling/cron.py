"""scheduling.cron — 定时调度器（s14）。

``CronScheduler`` 支持三种调度模式：
  at      — 在指定时间点执行一次（ISO 字符串、Unix 时间戳、datetime）
  every   — 每隔 N 秒执行一次
  cron    — 标准 cron 表达式（分 时 日 月 周），依赖 ``croniter`` 库；
            库不可用时降级为内置 ``_simple_cron_matches``（仅支持基本语法）

每次 ``tick()`` 遍历所有 job，对到期的 job：
  1. 运行 agent（``run_agent_turn``）
  2. 将 agent 回复通过 ``DeliveryQueue`` 投递给目标用户

状态持久化（last_fired_at / next_run_at）到 JSON 文件，跨重启不丢调度记录。
"""

from __future__ import annotations


import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.channels.delivery import DeliveryEntry, DeliveryQueue

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - exercised by monkeypatch in tests.
    croniter = None


CronKind = Literal["at", "every", "cron"]
CronTurnHandler = Callable[[str, str], Awaitable[str]]


class CronJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: CronKind
    schedule: str
    prompt: str
    channel: str
    to: str
    account_id: str = ""
    enabled: bool = True
    last_fired_at: float | None = None
    next_run_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CronScheduler:
    def __init__(
        self,
        *,
        delivery_queue: DeliveryQueue,
        run_agent_turn: CronTurnHandler,
        jobs: list[CronJob] | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        self.delivery_queue = delivery_queue
        self.run_agent_turn = run_agent_turn
        self.jobs = jobs or []
        self.state_path = Path(state_path).expanduser() if state_path else None
        self._apply_state()

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        *,
        delivery_queue: DeliveryQueue,
        run_agent_turn: CronTurnHandler,
        state_path: Path | str | None = None,
    ) -> "CronScheduler":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items = raw.get("jobs", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("cron file must contain a list or {'jobs': [...]}")
        jobs = [CronJob.model_validate(item) for item in items]
        return cls(
            delivery_queue=delivery_queue,
            run_agent_turn=run_agent_turn,
            jobs=jobs,
            state_path=state_path,
        )

    def add_at(
        self,
        *,
        job_id: str,
        when: datetime | float | str,
        prompt: str,
        channel: str,
        to: str,
        account_id: str = "",
    ) -> CronJob:
        timestamp = self._parse_at(when)
        job = CronJob(
            id=job_id,
            kind="at",
            schedule=str(timestamp),
            prompt=prompt,
            channel=channel,
            to=to,
            account_id=account_id,
            next_run_at=timestamp,
        )
        self.jobs.append(job)
        return job

    def add_every(
        self,
        *,
        job_id: str,
        interval_s: float,
        prompt: str,
        channel: str,
        to: str,
        account_id: str = "",
        now: float | None = None,
    ) -> CronJob:
        current = time.time() if now is None else now
        job = CronJob(
            id=job_id,
            kind="every",
            schedule=str(interval_s),
            prompt=prompt,
            channel=channel,
            to=to,
            account_id=account_id,
            next_run_at=current + interval_s,
        )
        self.jobs.append(job)
        return job

    def add_cron(
        self,
        *,
        job_id: str,
        cron_expr: str,
        prompt: str,
        channel: str,
        to: str,
        account_id: str = "",
    ) -> CronJob:
        job = CronJob(
            id=job_id,
            kind="cron",
            schedule=cron_expr,
            prompt=prompt,
            channel=channel,
            to=to,
            account_id=account_id,
        )
        self.jobs.append(job)
        return job

    async def tick(self, *, now: float | None = None) -> list[DeliveryEntry]:
        current = time.time() if now is None else now
        delivered: list[DeliveryEntry] = []
        for job in self.jobs:
            if not job.enabled or not self._is_due(job, current):
                continue
            job.last_fired_at = current
            job.next_run_at = self._next_run_at(job, current)
            self._save_state()
            session_id = f"cron:{job.id}"
            text = await self.run_agent_turn(job.prompt, session_id)
            if not text.strip():
                continue
            delivered.append(
                self.delivery_queue.enqueue(
                    channel=job.channel,
                    to=job.to,
                    account_id=job.account_id,
                    text=text,
                    metadata={
                        "source": "cron",
                        "cron_job_id": job.id,
                        "session_id": session_id,
                        **job.metadata,
                    },
                )
            )
        return delivered

    def _is_due(self, job: CronJob, now: float) -> bool:
        if job.kind == "at" and job.last_fired_at is not None:
            return False
        if job.kind in {"at", "every"}:
            if job.next_run_at is None:
                job.next_run_at = self._next_run_at(job, now)
            if job.next_run_at is None:
                return False
            return job.next_run_at <= now
        return self._cron_is_due(job, now)

    def _next_run_at(self, job: CronJob, now: float) -> float | None:
        if job.kind == "at":
            return self._parse_at(job.schedule)
        if job.kind == "every":
            return now + float(job.schedule)
        base = datetime.fromtimestamp(now, UTC)
        if croniter is not None:
            return croniter(job.schedule, base).get_next(datetime).timestamp()
        return _simple_next_cron_run(job.schedule, base)

    def _cron_is_due(self, job: CronJob, now: float) -> bool:
        base = datetime.fromtimestamp(now, UTC)
        if croniter is not None:
            previous = croniter(job.schedule, base).get_prev(datetime).timestamp()
            return job.last_fired_at is None or previous > job.last_fired_at
        minute_start = base.replace(second=0, microsecond=0).timestamp()
        if job.last_fired_at is not None and job.last_fired_at >= minute_start:
            return False
        return _simple_cron_matches(job.schedule, base)

    def _parse_at(self, when: datetime | float | str) -> float:
        if isinstance(when, datetime):
            return when.timestamp()
        if isinstance(when, int | float):
            return float(when)
        try:
            return float(when)
        except ValueError:
            pass
        return datetime.fromisoformat(when).timestamp()

    def _apply_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        raw = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        states = raw.get("jobs", raw) if isinstance(raw, dict) else {}
        if not isinstance(states, dict):
            return
        for job in self.jobs:
            state = states.get(job.id)
            if not isinstance(state, dict):
                continue
            job.last_fired_at = state.get("last_fired_at")
            job.next_run_at = state.get("next_run_at")

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "jobs": {
                job.id: {
                    "last_fired_at": job.last_fired_at,
                    "next_run_at": job.next_run_at,
                }
                for job in self.jobs
            },
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            dir=self.state_path.parent,
            prefix=f".tmp.{self.state_path.stem}.",
            suffix=self.state_path.suffix or ".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.state_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _simple_next_cron_run(expression: str, base: datetime) -> float | None:
    cursor = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _simple_cron_matches(expression, cursor):
            return cursor.timestamp()
        cursor += timedelta(minutes=1)
    return None


def _simple_cron_matches(expression: str, when: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have five fields")
    values = (
        when.minute,
        when.hour,
        when.day,
        when.month,
        (when.weekday() + 1) % 7,
    )
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    return all(
        _simple_cron_field_matches(field, value, low, high)
        for field, value, (low, high) in zip(fields, values, ranges, strict=True)
    )


def _simple_cron_field_matches(field: str, value: int, low: int, high: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        if step <= 0:
            raise ValueError("cron step must be positive")
        return (value - low) % step == 0
    allowed = {_normalize_cron_value(part, high) for part in field.split(",")}
    return value in allowed


def _normalize_cron_value(raw: str, high: int) -> int:
    value = int(raw)
    if high == 7 and value == 7:
        return 0
    return value
