"""scheduling.activity — 会话活跃状态追踪，供心跳检测使用。"""
from __future__ import annotations

import time

from agent.channels.base import InboundMessage


class ActivityTracker:
    """追踪每个 (channel, account_id, peer_id) 三元组的活跃状态。

    心跳检测在决定是否发送主动消息前，需要知道：
    - 是否有正在进行的 agent 任务（避免打断）
    - 用户最近是否发过消息（避免在用户刚发言后立即推送）

    生命周期：
      mark_inbound()    — 收到用户消息时调用，记录时间戳
      mark_agent_start()— agent 开始处理该消息时调用
      mark_agent_end()  — agent 处理完毕时调用
      is_busy()         — 心跳 tick 时调用，判断是否应跳过本次心跳
    """
    """Track per-conversation request lifecycle for heartbeat idle detection."""

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
