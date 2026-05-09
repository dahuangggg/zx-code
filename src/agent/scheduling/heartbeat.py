"""scheduling.heartbeat — 周期性心跳（主动推送）。

``HeartbeatRunner.tick()`` 被 channel loop 按间隔调用：
  1. 检查间隔是否到期（next_run_at）
  2. 检查 ActivityTracker 判断用户是否空闲（min_idle_s 控制冷静期）
  3. 在独立 session_id 下运行 agent，执行 heartbeat prompt
  4. 若 agent 回复不是 sentinel（默认 "HEARTBEAT_OK"），则推送给用户

典型用途：
  - 定期检查进行中的任务是否有需要告知用户的进展
  - 长时间无用户消息时主动发送状态更新
"""

from __future__ import annotations


import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.scheduling.activity import ActivityTracker
from agent.channels.delivery import DeliveryEntry, DeliveryQueue


HeartbeatTurnHandler = Callable[[str, str], Awaitable[str]]

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
