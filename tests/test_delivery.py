from __future__ import annotations

from typing import Any

from agent.channels.base import Channel, ChannelManager, InboundMessage
from agent.delivery import DeliveryQueue, DeliveryRunner, chunk_message
from agent.gateway import AgentRouteConfig, BindingTable, Gateway


class FakeChannel(Channel):
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def receive(self) -> InboundMessage | None:
        return None

    async def send(self, to: str, text: str, **kwargs: Any) -> bool:
        self.sent.append((to, text, kwargs))
        return not self.fail


def test_chunk_message_splits_on_newlines() -> None:
    assert chunk_message("telegram", "hello\nworld", limit=5) == ["hello", "world"]


def test_delivery_queue_writes_then_marks_sent(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    entry = queue.enqueue(channel="fake", to="peer", text="hello")

    queued_path = tmp_path / "queued" / f"{entry.id}.json"
    assert queued_path.exists()

    sent = queue.mark_sent(entry.id)

    assert sent is not None
    assert sent.status == "sent"
    assert not queued_path.exists()
    assert (tmp_path / "sent" / f"{entry.id}.json").exists()


def test_delivery_queue_retries_then_moves_to_failed(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, max_attempts=2, base_delay_s=10, jitter_s=0)
    entry = queue.enqueue(channel="fake", to="peer", text="hello")

    retry = queue.mark_retry(entry.id, error="first", now=100)
    failed = queue.mark_retry(entry.id, error="second", now=200)

    assert retry is not None
    assert retry.retry_count == 1
    assert retry.next_retry_at == 110
    assert failed is not None
    assert failed.status == "failed"
    assert failed.retry_count == 2
    assert (tmp_path / "failed" / f"{entry.id}.json").exists()


async def test_delivery_runner_sends_ready_entries(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    entry = queue.enqueue(channel="fake", to="peer", text="hello")
    channel = FakeChannel()
    manager = ChannelManager()
    manager.register(channel)
    runner = DeliveryRunner(queue=queue, channel_manager=manager)

    delivered = await runner.deliver_ready_once(now=0)

    assert delivered == 1
    assert channel.sent[0][0] == "peer"
    assert channel.sent[0][1] == "hello"
    assert queue.get(entry.id).status == "sent"


async def test_delivery_runner_keeps_failed_send_queued(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, max_attempts=3, base_delay_s=5, jitter_s=0)
    entry = queue.enqueue(channel="fake", to="peer", text="hello")
    channel = FakeChannel(fail=True)
    manager = ChannelManager()
    manager.register(channel)
    runner = DeliveryRunner(queue=queue, channel_manager=manager)

    delivered = await runner.deliver(entry.id)
    reloaded = queue.get(entry.id)

    assert not delivered
    assert reloaded is not None
    assert reloaded.status == "queued"
    assert reloaded.retry_count == 1


async def test_gateway_uses_delivery_queue_before_send(tmp_path) -> None:
    queue = DeliveryQueue(tmp_path, jitter_s=0)
    channel = FakeChannel()
    manager = ChannelManager()
    manager.register(channel)
    runner = DeliveryRunner(queue=queue, channel_manager=manager)

    async def run_agent_turn(inbound: InboundMessage, agent_id: str, session_id: str) -> str:
        return "reply"

    gateway = Gateway(
        channel_manager=manager,
        binding_table=BindingTable(),
        agent_configs={"default": AgentRouteConfig(agent_id="default")},
        run_agent_turn=run_agent_turn,
        delivery_queue=queue,
        delivery_runner=runner,
    )

    result = await gateway.handle_inbound(
        InboundMessage(
            text="hello",
            sender_id="u",
            channel="fake",
            account_id="a",
            peer_id="p",
        )
    )

    assert result.delivered
    assert channel.sent[0][1] == "reply"
    sent_entries = list((tmp_path / "sent").glob("*.json"))
    assert len(sent_entries) == 1
