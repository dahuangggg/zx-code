from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.channels.base import InboundMessage
from agent.delivery import DeliveryEntry, DeliveryQueue


HeartbeatTurnHandler = Callable[[str, str], Awaitable[str]]


class ActivityTracker:
    def __init__(self) -> None:
        self._active: set[tuple[str, str, str]] = set()
        self._last_user_at: dict[tuple[str, str, str], float] = {}

    def mark_inbound(self, inbound: InboundMessage, *, now: float | None = None) -> None:
        self._last_user_at[self._key(inbound.channel, inbound.account_id, inbound.peer_id)] = (
            time.time() if now is None else now
        )

    def mark_agent_start(self, inbound: InboundMessage) -> None:
        self._active.add(self._key(inbound.channel, inbound.account_id, inbound.peer_id))

    def mark_agent_end(self, inbound: InboundMessage) -> None:
        self._active.discard(self._key(inbound.channel, inbound.account_id, inbound.peer_id))

    def is_busy(
        self,
        *,
        channel: str,
        account_id: str,
        peer_id: str,
        min_idle_s: float,
        now: float | None = None,
    ) -> bool:
        key = self._key(channel, account_id, peer_id)
        if key in self._active:
            return True
        last_user_at = self._last_user_at.get(key)
        if last_user_at is None:
            return False
        current = time.time() if now is None else now
        return current - last_user_at < min_idle_s

    def _key(self, channel: str, account_id: str, peer_id: str) -> tuple[str, str, str]:
        return channel, account_id, peer_id


class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_s: float = 300.0
    min_idle_s: float = 30.0
    channel: str = ""
    to: str = ""
    account_id: str = ""
    prompt: str = "Heartbeat check. Reply HEARTBEAT_OK if no user-facing update is needed."
    sentinel: str = "HEARTBEAT_OK"


class HeartbeatRunner:
    def __init__(
        self,
        *,
        config: HeartbeatConfig,
        delivery_queue: DeliveryQueue,
        run_agent_turn: HeartbeatTurnHandler,
        activity_tracker: ActivityTracker | None = None,
    ) -> None:
        self.config = config
        self.delivery_queue = delivery_queue
        self.run_agent_turn = run_agent_turn
        self.activity_tracker = activity_tracker
        self.next_run_at = 0.0

    async def tick(self, *, now: float | None = None) -> DeliveryEntry | None:
        current = time.time() if now is None else now
        if not self.config.enabled:
            return None
        if current < self.next_run_at:
            return None
        self.next_run_at = current + self.config.interval_s
        if not self.config.channel or not self.config.to:
            return None
        if self.activity_tracker and self.activity_tracker.is_busy(
            channel=self.config.channel,
            account_id=self.config.account_id,
            peer_id=self.config.to,
            min_idle_s=self.config.min_idle_s,
            now=current,
        ):
            return None

        session_id = f"heartbeat:{self.config.channel}:{self.config.to}"
        text = await self.run_agent_turn(self.config.prompt, session_id)
        if not text.strip() or text.strip() == self.config.sentinel:
            return None
        return self.delivery_queue.enqueue(
            channel=self.config.channel,
            to=self.config.to,
            account_id=self.config.account_id,
            text=text,
            metadata={
                "source": "heartbeat",
                "session_id": session_id,
            },
        )
