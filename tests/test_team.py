from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.agents.team import MessageBus, Team, TeamMember, TeamMessage


# ---------------------------------------------------------------------------
# TeamMessage
# ---------------------------------------------------------------------------


def test_team_message_has_auto_id_and_timestamp() -> None:
    msg = TeamMessage(from_agent="a", to_agent="b", type="request", content="hi")
    assert len(msg.id) == 12
    assert msg.created_at


def test_team_message_roundtrips_json() -> None:
    msg = TeamMessage(
        from_agent="a", to_agent="b", type="response", content="ok", reply_to="abc123"
    )
    restored = TeamMessage.model_validate_json(msg.model_dump_json())
    assert restored == msg


# ---------------------------------------------------------------------------
# MessageBus — unicast / broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_unicast_delivers_to_target_only() -> None:
    bus = MessageBus()
    q_a = bus.subscribe("a")
    q_b = bus.subscribe("b")

    msg = TeamMessage(from_agent="a", to_agent="b", type="broadcast", content="hello")
    await bus.publish(msg)

    assert q_b.qsize() == 1
    assert q_a.qsize() == 0


@pytest.mark.asyncio
async def test_bus_broadcast_reaches_all_except_sender() -> None:
    bus = MessageBus()
    bus.subscribe("sender")
    q_x = bus.subscribe("x")
    q_y = bus.subscribe("y")

    await bus.broadcast("news", from_agent="sender")

    assert q_x.qsize() == 1
    assert q_y.qsize() == 1


@pytest.mark.asyncio
async def test_bus_publish_to_unknown_agent_is_silently_ignored() -> None:
    bus = MessageBus()
    msg = TeamMessage(from_agent="a", to_agent="nobody", type="request", content="?")
    # Should not raise
    await bus.publish(msg)


# ---------------------------------------------------------------------------
# MessageBus — request / respond
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_request_response_roundtrip() -> None:
    bus = MessageBus()
    bus.subscribe("worker")

    async def responder() -> None:
        inbox = bus._queues["worker"]
        msg = await asyncio.wait_for(inbox.get(), timeout=2.0)
        await bus.respond(msg, "done", from_agent="worker")

    asyncio.create_task(responder())
    response = await bus.request("coordinator", "worker", "do work", timeout_s=2.0)

    assert response.content == "done"
    assert response.reply_to is not None
    assert response.from_agent == "worker"


@pytest.mark.asyncio
async def test_bus_request_raises_on_timeout() -> None:
    bus = MessageBus()
    bus.subscribe("slow")

    with pytest.raises(TimeoutError, match="slow"):
        await bus.request("coordinator", "slow", "ping", timeout_s=0.05)


# ---------------------------------------------------------------------------
# MessageBus — JSONL persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_appends_messages_to_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    bus = MessageBus(log_path=log)
    bus.subscribe("b")

    msg = TeamMessage(from_agent="a", to_agent="b", type="broadcast", content="hello")
    await bus.publish(msg)

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"hello"' in lines[0]


@pytest.mark.asyncio
async def test_bus_load_history_replays_all_messages(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    bus = MessageBus(log_path=log)
    bus.subscribe("b")

    for i in range(3):
        msg = TeamMessage(
            from_agent="a", to_agent="b", type="broadcast", content=f"msg{i}"
        )
        await bus.publish(msg)

    # Fresh bus reading the same file
    bus2 = MessageBus(log_path=log)
    history = bus2.load_history()
    assert len(history) == 3
    assert [m.content for m in history] == ["msg0", "msg1", "msg2"]


# ---------------------------------------------------------------------------
# TeamMember
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_member_handles_request_and_replies() -> None:
    bus = MessageBus()

    async def echo(task: str, session_id: str) -> str:
        return f"echo:{task}"

    member = TeamMember("worker", bus=bus, run_agent=echo)
    task = asyncio.create_task(member.listen())

    request = TeamMessage(
        from_agent="coordinator", to_agent="worker", type="request", content="hello"
    )
    coordinator_inbox = bus.subscribe("coordinator")
    await bus.publish(request)

    response = await asyncio.wait_for(coordinator_inbox.get(), timeout=2.0)
    member.stop()
    await task

    assert response.reply_to == request.id
    assert response.content == "echo:hello"
    assert response.from_agent == "worker"


@pytest.mark.asyncio
async def test_team_member_handles_broadcast_without_replying() -> None:
    bus = MessageBus()
    received: list[str] = []

    async def record(task: str, session_id: str) -> str:
        received.append(task)
        return "ok"

    member = TeamMember("listener", bus=bus, run_agent=record)
    task = asyncio.create_task(member.listen())

    await bus.broadcast("announcement", from_agent="coordinator")
    await asyncio.sleep(0.1)
    member.stop()
    await task

    assert received == ["announcement"]


@pytest.mark.asyncio
async def test_team_member_returns_error_string_on_agent_failure() -> None:
    bus = MessageBus()

    async def failing(task: str, session_id: str) -> str:
        raise RuntimeError("agent failed")

    member = TeamMember("buggy", bus=bus, run_agent=failing)
    listener_task = asyncio.create_task(member.listen())

    request = TeamMessage(
        from_agent="coord", to_agent="buggy", type="request", content="go"
    )
    coord_inbox = bus.subscribe("coord")
    await bus.publish(request)

    response = await asyncio.wait_for(coord_inbox.get(), timeout=2.0)
    member.stop()
    await listener_task

    assert "[error]" in response.content
    assert "agent failed" in response.content


# ---------------------------------------------------------------------------
# Team — high-level orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_dispatch_returns_agent_reply() -> None:
    team = Team()

    async def double(task: str, session_id: str) -> str:
        return task * 2

    team.add_member("doubler", double)
    team.start()

    result = await team.dispatch("doubler", "ab", timeout_s=2.0)
    await team.shutdown()

    assert result == "abab"


@pytest.mark.asyncio
async def test_team_broadcast_reaches_all_members() -> None:
    team = Team()
    log: list[str] = []

    async def recorder(task: str, session_id: str) -> str:
        log.append(task)
        return "ok"

    team.add_member("alice", recorder)
    team.add_member("bob", recorder)
    team.start()

    await team.broadcast("hello team")
    await asyncio.sleep(0.1)
    await team.shutdown()

    assert log.count("hello team") == 2


@pytest.mark.asyncio
async def test_team_shutdown_is_graceful() -> None:
    team = Team()

    async def slow(task: str, session_id: str) -> str:
        await asyncio.sleep(10)
        return "done"

    team.add_member("slow", slow)
    team.start()

    # shutdown should complete quickly without waiting for in-flight work
    await asyncio.wait_for(team.shutdown(), timeout=2.0)


@pytest.mark.asyncio
async def test_team_persists_messages_to_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "team.jsonl"
    team = Team(log_path=log)

    async def echo(task: str, session_id: str) -> str:
        return f"echo:{task}"

    team.add_member("agent", echo)
    team.start()

    await team.dispatch("agent", "ping", timeout_s=2.0)
    await team.shutdown()

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    # request + response = 2 lines
    assert len(lines) == 2
