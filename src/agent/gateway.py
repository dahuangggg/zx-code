from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.channels.base import ChannelManager, InboundMessage
from agent.delivery import DeliveryQueue, DeliveryRunner
from agent.heartbeat import ActivityTracker


DMScope = Literal[
    "per-account-channel-peer",
    "per-channel-peer",
    "per-peer",
    "per-agent",
]

AgentTurnHandler = Callable[[InboundMessage, str, str], Awaitable[str]]


def _target_peer(peer_id: str, guild_id: str = "") -> tuple[str, str]:
    if guild_id:
        return "group", guild_id
    return "direct", peer_id


def build_session_key(
    *,
    agent_id: str,
    channel: str,
    account_id: str,
    peer_id: str,
    dm_scope: DMScope,
    guild_id: str = "",
) -> str:
    target_type, target_id = _target_peer(peer_id, guild_id)
    if dm_scope == "per-account-channel-peer":
        return f"agent:{agent_id}:{channel}:{account_id}:{target_type}:{target_id}"
    if dm_scope == "per-channel-peer":
        return f"agent:{agent_id}:{channel}:{target_type}:{target_id}"
    if dm_scope == "per-peer":
        return f"agent:{agent_id}:{target_type}:{target_id}"
    return f"agent:{agent_id}:main"


class AgentRouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    dm_scope: DMScope = "per-account-channel-peer"


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    channel: str = ""
    account_id: str = ""
    guild_id: str = ""
    peer_id: str = ""
    force: bool = False

    def matches(
        self,
        *,
        channel: str = "",
        account_id: str = "",
        guild_id: str = "",
        peer_id: str = "",
    ) -> bool:
        checks = {
            "channel": channel,
            "account_id": account_id,
            "guild_id": guild_id,
            "peer_id": peer_id,
        }
        for field_name, value in checks.items():
            expected = getattr(self, field_name)
            if expected and expected != value:
                return False
        return True

    def score(self) -> tuple[int, int]:
        score = 0
        if self.channel:
            score += 1
        if self.account_id:
            score += 10
        if self.guild_id:
            score += 100
        if self.peer_id:
            score += 1000
        force_score = 10000 if self.force else 0
        return force_score, score


class BindingTable:
    def __init__(self, *, default_agent_id: str = "default") -> None:
        self.default_agent_id = default_agent_id
        self.bindings: list[Binding] = []

    def add(self, binding: Binding) -> None:
        self.bindings.append(binding)

    def switch_agent(
        self,
        *,
        agent_id: str,
        channel: str,
        account_id: str = "",
        guild_id: str = "",
        peer_id: str = "",
        force: bool = True,
    ) -> Binding:
        binding = Binding(
            agent_id=agent_id,
            channel=channel,
            account_id=account_id,
            guild_id=guild_id,
            peer_id=peer_id,
            force=force,
        )
        self.add(binding)
        return binding

    def clear(
        self,
        *,
        channel: str = "",
        account_id: str = "",
        guild_id: str = "",
        peer_id: str = "",
    ) -> int:
        before = len(self.bindings)
        self.bindings = [
            binding
            for binding in self.bindings
            if not (
                binding.channel == channel
                and binding.account_id == account_id
                and binding.guild_id == guild_id
                and binding.peer_id == peer_id
            )
        ]
        return before - len(self.bindings)

    def resolve(
        self,
        *,
        channel: str = "",
        account_id: str = "",
        guild_id: str = "",
        peer_id: str = "",
        force_agent_id: str | None = None,
    ) -> str:
        if force_agent_id:
            return force_agent_id

        matches = [
            (index, binding)
            for index, binding in enumerate(self.bindings)
            if binding.matches(
                channel=channel,
                account_id=account_id,
                guild_id=guild_id,
                peer_id=peer_id,
            )
        ]
        if not matches:
            return self.default_agent_id

        _, best = max(matches, key=lambda item: (*item[1].score(), item[0]))
        return best.agent_id

    def resolve_inbound(
        self,
        inbound: InboundMessage,
        *,
        force_agent_id: str | None = None,
    ) -> str:
        return self.resolve(
            channel=inbound.channel,
            account_id=inbound.account_id,
            guild_id=inbound.guild_id,
            peer_id=inbound.peer_id,
            force_agent_id=force_agent_id,
        )


class GatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbound: InboundMessage
    agent_id: str
    session_id: str
    reply: str
    delivered: bool


class Gateway:
    def __init__(
        self,
        *,
        channel_manager: ChannelManager,
        binding_table: BindingTable,
        agent_configs: dict[str, AgentRouteConfig],
        run_agent_turn: AgentTurnHandler,
        delivery_queue: DeliveryQueue | None = None,
        delivery_runner: DeliveryRunner | None = None,
        activity_tracker: ActivityTracker | None = None,
    ) -> None:
        self.channel_manager = channel_manager
        self.binding_table = binding_table
        self.agent_configs = agent_configs
        self.run_agent_turn = run_agent_turn
        self.delivery_queue = delivery_queue
        self.delivery_runner = delivery_runner
        self.activity_tracker = activity_tracker

    def agent_config(self, agent_id: str) -> AgentRouteConfig:
        if agent_id not in self.agent_configs:
            self.agent_configs[agent_id] = AgentRouteConfig(agent_id=agent_id)
        return self.agent_configs[agent_id]

    async def handle_inbound(
        self,
        inbound: InboundMessage,
        *,
        force_agent_id: str | None = None,
    ) -> GatewayResult:
        if self.activity_tracker is not None:
            self.activity_tracker.mark_inbound(inbound)
            self.activity_tracker.mark_agent_start(inbound)
        agent_id = self.binding_table.resolve_inbound(
            inbound,
            force_agent_id=force_agent_id,
        )
        config = self.agent_config(agent_id)
        session_id = build_session_key(
            agent_id=agent_id,
            channel=inbound.channel,
            account_id=inbound.account_id,
            peer_id=inbound.peer_id,
            guild_id=inbound.guild_id,
            dm_scope=config.dm_scope,
        )
        try:
            reply = await self.run_agent_turn(inbound, agent_id, session_id)
        finally:
            if self.activity_tracker is not None:
                self.activity_tracker.mark_agent_end(inbound)

        delivered = await self._deliver_reply(
            inbound,
            reply,
            agent_id=agent_id,
            session_id=session_id,
        )
        return GatewayResult(
            inbound=inbound,
            agent_id=agent_id,
            session_id=session_id,
            reply=reply,
            delivered=delivered,
        )

    async def receive_once(
        self,
        channel_name: str,
        *,
        force_agent_id: str | None = None,
    ) -> GatewayResult | None:
        channel = self.channel_manager.get(channel_name)
        inbound = await channel.receive()
        if inbound is None:
            return None
        return await self.handle_inbound(inbound, force_agent_id=force_agent_id)

    async def drain_delivery(self) -> int:
        if self.delivery_runner is None:
            return 0
        return await self.delivery_runner.deliver_ready_once()

    async def _deliver_reply(
        self,
        inbound: InboundMessage,
        reply: str,
        *,
        agent_id: str,
        session_id: str,
    ) -> bool:
        metadata = {
            "inbound": inbound.model_dump(mode="json"),
            "agent_id": agent_id,
            "session_id": session_id,
        }
        if self.delivery_queue is not None and self.delivery_runner is not None:
            entry = self.delivery_queue.enqueue(
                channel=inbound.channel,
                to=inbound.peer_id,
                account_id=inbound.account_id,
                text=reply,
                metadata=metadata,
            )
            return await self.delivery_runner.deliver(entry.id)

        channel = self.channel_manager.get(inbound.channel)
        return await channel.send(
            inbound.peer_id,
            reply,
            **metadata,
        )
