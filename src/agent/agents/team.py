"""s15-s17: Agent Teams — MessageBus + TeamMember + Team.

Design: asyncio.Queue for runtime routing, JSONL file for persistence.
- MessageBus handles routing and request-response matching via asyncio.Future.
- TeamMember wraps any async agent callable and processes inbound messages.
- Team orchestrates members and exposes dispatch / broadcast helpers.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


TeamMessageType = Literal["request", "response", "broadcast"]

# (task, session_id) -> reply text — same signature as SubagentRunner internals
AgentRunFn = Callable[[str, str], Awaitable[str]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TeamMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    from_agent: str
    to_agent: str  # agent_id, or "*" for broadcast
    type: TeamMessageType
    content: str
    reply_to: str | None = None  # set on "response" messages
    created_at: str = Field(default_factory=_now)


class MessageBus:
    """
    In-process message bus for agent teams.

    Routing rules:
    - ``to_agent == "*"``  → broadcast to every subscriber except the sender.
    - ``to_agent == <id>`` → unicast to that subscriber's queue.
    - Responses (``reply_to`` set) bypass queues and resolve a Future directly,
      so request() callers are never blocked by unrelated messages.

    Persistence: every published message is appended as one JSON line to
    ``log_path`` (if provided). The file is fsync'd on each write for safety.
    """

    def __init__(self, log_path: Path | str | None = None) -> None:
        self._queues: dict[str, asyncio.Queue[TeamMessage]] = {}
        self._pending: dict[str, asyncio.Future[TeamMessage]] = {}
        self._log_path = Path(log_path) if log_path else None

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, agent_id: str) -> asyncio.Queue[TeamMessage]:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
        return self._queues[agent_id]

    def unsubscribe(self, agent_id: str) -> None:
        self._queues.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Publish / request / respond
    # ------------------------------------------------------------------

    async def publish(self, message: TeamMessage) -> None:
        """Route *message* and append to the JSONL log."""
        # Responses short-circuit to a waiting Future, not a queue.
        if message.reply_to is not None:
            future = self._pending.pop(message.reply_to, None)
            if future is not None and not future.done():
                future.set_result(message)
                self._append_log(message)
                return

        if message.to_agent == "*":
            for agent_id, queue in self._queues.items():
                if agent_id != message.from_agent:
                    await queue.put(message)
        elif message.to_agent in self._queues:
            await self._queues[message.to_agent].put(message)

        self._append_log(message)

    async def request(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        *,
        timeout_s: float = 30.0,
    ) -> TeamMessage:
        """Send a request to *to_agent* and wait for its response.

        Raises ``TimeoutError`` if no response arrives within *timeout_s*.
        """
        msg = TeamMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            type="request",
            content=content,
        )
        loop = asyncio.get_event_loop()
        future: asyncio.Future[TeamMessage] = loop.create_future()
        self._pending[msg.id] = future

        await self.publish(msg)

        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending.pop(msg.id, None)
            raise TimeoutError(
                f"Agent {to_agent!r} did not respond within {timeout_s}s"
            )

    async def respond(
        self,
        original: TeamMessage,
        content: str,
        *,
        from_agent: str,
    ) -> None:
        """Publish a response to *original*."""
        response = TeamMessage(
            from_agent=from_agent,
            to_agent=original.from_agent,
            type="response",
            content=content,
            reply_to=original.id,
        )
        await self.publish(response)

    async def broadcast(self, content: str, *, from_agent: str) -> None:
        """Publish a broadcast message to all subscribers except *from_agent*."""
        msg = TeamMessage(
            from_agent=from_agent,
            to_agent="*",
            type="broadcast",
            content=content,
        )
        await self.publish(msg)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load_history(self) -> list[TeamMessage]:
        """Return all messages persisted to the JSONL log."""
        if self._log_path is None or not self._log_path.exists():
            return []
        messages: list[TeamMessage] = []
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                messages.append(TeamMessage.model_validate_json(line))
        return messages

    def _append_log(self, message: TeamMessage) -> None:
        if self._log_path is None:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        line = message.model_dump_json() + "\n"
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())


class TeamMember:
    """
    A single team participant.

    Subscribes to the bus and runs an agent callable for every inbound
    ``request`` or ``broadcast`` it receives. Responses are sent back
    automatically for ``request`` messages.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        bus: MessageBus,
        run_agent: AgentRunFn,
    ) -> None:
        self.agent_id = agent_id
        self._bus = bus
        self._run_agent = run_agent
        self._inbox = bus.subscribe(agent_id)
        self._stop = asyncio.Event()

    async def listen(self) -> None:
        """Process messages until ``stop()`` is called."""
        while not self._stop.is_set():
            try:
                message = await asyncio.wait_for(self._inbox.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._handle(message)

    async def _handle(self, message: TeamMessage) -> None:
        session_id = f"team:{self.agent_id}:{message.id}"
        try:
            reply = await self._run_agent(message.content, session_id)
        except Exception as exc:
            reply = f"[error] {exc}"

        if message.type == "request":
            await self._bus.respond(message, reply, from_agent=self.agent_id)

    def stop(self) -> None:
        self._stop.set()


class Team:
    """
    Orchestrates a group of TeamMembers sharing a MessageBus.

    Typical usage::

        team = Team(log_path=".team/messages.jsonl")
        team.add_member("researcher", run_researcher)
        team.add_member("writer", run_writer)
        team.start()

        answer = await team.dispatch("researcher", "Summarise PEP 703")
        await team.shutdown()
    """

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.bus = MessageBus(log_path=log_path)
        self._members: dict[str, TeamMember] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def add_member(self, agent_id: str, run_agent: AgentRunFn) -> TeamMember:
        """Register a new member. Must be called before ``start()``."""
        member = TeamMember(agent_id=agent_id, bus=self.bus, run_agent=run_agent)
        self._members[agent_id] = member
        return member

    def start(self) -> None:
        """Launch all member ``listen()`` loops as asyncio background tasks."""
        for agent_id, member in self._members.items():
            existing = self._tasks.get(agent_id)
            if existing is None or existing.done():
                self._tasks[agent_id] = asyncio.create_task(
                    member.listen(),
                    name=f"team_member:{agent_id}",
                )

    async def dispatch(
        self,
        to_agent: str,
        content: str,
        *,
        from_agent: str = "coordinator",
        timeout_s: float = 30.0,
    ) -> str:
        """Send a request to *to_agent* and return its reply text."""
        response = await self.bus.request(
            from_agent, to_agent, content, timeout_s=timeout_s
        )
        return response.content

    async def broadcast(
        self,
        content: str,
        *,
        from_agent: str = "coordinator",
    ) -> None:
        """Broadcast *content* to all members."""
        await self.bus.broadcast(content, from_agent=from_agent)

    async def shutdown(self) -> None:
        """Signal all members to stop and wait for their tasks to finish."""
        for member in self._members.values():
            member.stop()
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
