"""进程内 agent team 协调。

``MessageBus`` 通过 asyncio 队列路由 request、response 和 broadcast。
可选 JSONL 持久化让 team 通信可检查，同时避免核心 agent loop 理解 team 语义。
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


TeamMessageType = Literal["request", "response", "broadcast"]

AgentRunFn = Callable[[str, str], Awaitable[str]]
"""在隔离 session 中运行 team 成员任务的可调用签名。"""


def _now() -> str:
    """返回 UTC ISO 时间戳。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


class TeamMessage(BaseModel):
    """Team 内部消息；request/response 通过 reply_to 配对。"""

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
    agent team 的进程内消息总线。

    路由规则：
    - ``to_agent == "*"``：广播给除发送者外的所有订阅者。
    - ``to_agent == <id>``：单播到该成员队列。
    - 设置了 ``reply_to`` 的响应会绕过队列，直接完成等待中的 Future，
      避免 request() 调用方被无关消息阻塞。

    持久化：如果配置了 ``log_path``，每条发布消息都会追加为一行 JSON；
    每次写入后都会 fsync，降低进程崩溃造成日志损坏的概率。
    """

    def __init__(self, log_path: Path | str | None = None) -> None:
        self._queues: dict[str, asyncio.Queue[TeamMessage]] = {}
        self._pending: dict[str, asyncio.Future[TeamMessage]] = {}
        self._log_path = Path(log_path) if log_path else None

    def subscribe(self, agent_id: str) -> asyncio.Queue[TeamMessage]:
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
        return self._queues[agent_id]

    def unsubscribe(self, agent_id: str) -> None:
        self._queues.pop(agent_id, None)

    async def publish(self, message: TeamMessage) -> None:
        """路由消息，并追加到 JSONL 日志。"""
        # 响应消息直接完成等待中的 Future，不再进入普通队列。
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
        """向目标成员发送请求并等待响应。

        如果在 ``timeout_s`` 内没有响应，则抛出 ``TimeoutError``。
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
        """针对原始请求发布响应。"""
        response = TeamMessage(
            from_agent=from_agent,
            to_agent=original.from_agent,
            type="response",
            content=content,
            reply_to=original.id,
        )
        await self.publish(response)

    async def broadcast(self, content: str, *, from_agent: str) -> None:
        """向除发送者外的所有成员发布广播消息。"""
        msg = TeamMessage(
            from_agent=from_agent,
            to_agent="*",
            type="broadcast",
            content=content,
        )
        await self.publish(msg)

    def load_history(self) -> list[TeamMessage]:
        """读取 JSONL 日志中持久化的全部消息。"""
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


@dataclass(frozen=True)
class TeamMemberInfo:
    """注入 prompt 和 team_list 工具的成员元数据。"""

    agent_id: str
    role: str = ""


class TeamMember:
    """
    单个 team 参与者。

    成员订阅消息总线，并对收到的每个 ``request`` 或 ``broadcast`` 执行注入的
    agent callable。对于 ``request`` 消息，成员会自动回传响应。
    """

    def __init__(
        self,
        agent_id: str,
        *,
        bus: MessageBus,
        run_agent: AgentRunFn,
        parent_session_id: str = "",
    ) -> None:
        self.agent_id = agent_id
        self._bus = bus
        self._run_agent = run_agent
        self._parent_session_id = parent_session_id
        self._inbox = bus.subscribe(agent_id)
        self._stop = asyncio.Event()

    async def listen(self) -> None:
        """持续处理消息，直到调用 ``stop()``。"""
        while not self._stop.is_set():
            try:
                message = await asyncio.wait_for(self._inbox.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._handle(message)

    async def _handle(self, message: TeamMessage) -> None:
        session_parts = ["team"]
        if self._parent_session_id:
            session_parts.append(self._parent_session_id)
        session_parts.extend([self.agent_id, message.id])
        session_id = ":".join(session_parts)
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
    协调共享同一个 MessageBus 的 TeamMember 集合。

    典型用法::

        team = Team(log_path=".team/messages.jsonl")
        team.add_member("researcher", run_researcher)
        team.add_member("writer", run_writer)
        team.start()

        answer = await team.dispatch("researcher", "Summarise PEP 703")
        await team.shutdown()
    """

    def __init__(
        self,
        log_path: Path | str | None = None,
        *,
        parent_session_id: str = "",
    ) -> None:
        self.bus = MessageBus(log_path=log_path)
        self._members: dict[str, TeamMember] = {}
        self._member_info: dict[str, TeamMemberInfo] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._parent_session_id = parent_session_id

    def add_member(
        self,
        agent_id: str,
        run_agent: AgentRunFn,
        *,
        role: str = "",
    ) -> TeamMember:
        """注册新成员；必须在 ``start()`` 之前调用。"""
        member = TeamMember(
            agent_id=agent_id,
            bus=self.bus,
            run_agent=run_agent,
            parent_session_id=self._parent_session_id,
        )
        self._members[agent_id] = member
        self._member_info[agent_id] = TeamMemberInfo(agent_id=agent_id, role=role)
        return member

    def members_summary(self) -> list[dict[str, str]]:
        """返回适合 prompt 和工具展示的成员元数据。"""
        return [
            {"agent_id": info.agent_id, "role": info.role}
            for info in self._member_info.values()
        ]

    def start(self) -> None:
        """以 asyncio 后台任务启动所有成员的 ``listen()`` 循环。"""
        for agent_id, member in self._members.items():
            existing = self._tasks.get(agent_id)
            if existing is None or existing.done():
                self._tasks[agent_id] = asyncio.create_task(
                    member.listen(),
                    name=f"team_member:{agent_id}",
                )

    def ensure_started(self) -> None:
        """如果成员循环尚未运行，则启动它们。"""
        self.start()

    async def dispatch(
        self,
        to_agent: str,
        content: str,
        *,
        from_agent: str = "coordinator",
        timeout_s: float = 30.0,
    ) -> str:
        """向目标成员发送请求，并返回其回复文本。"""
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
        """向所有成员广播内容。"""
        await self.bus.broadcast(content, from_agent=from_agent)

    async def shutdown(self) -> None:
        """通知所有成员停止，并等待它们的任务结束。"""
        for member in self._members.values():
            member.stop()
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
