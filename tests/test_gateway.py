from __future__ import annotations

from agent.channels import ChannelManager, CLIChannel, InboundMessage
from agent.gateway import (
    AgentRouteConfig,
    Binding,
    BindingTable,
    Gateway,
    build_session_key,
)


def test_build_session_key_respects_dm_scope() -> None:
    assert (
        build_session_key(
            agent_id="coder",
            channel="telegram",
            account_id="bot-a",
            peer_id="123",
            dm_scope="per-account-channel-peer",
        )
        == "agent:coder:telegram:bot-a:direct:123"
    )
    assert (
        build_session_key(
            agent_id="coder",
            channel="telegram",
            account_id="bot-a",
            peer_id="123",
            guild_id="-100",
            dm_scope="per-channel-peer",
        )
        == "agent:coder:telegram:group:-100"
    )
    assert (
        build_session_key(
            agent_id="coder",
            channel="telegram",
            account_id="bot-a",
            peer_id="123",
            dm_scope="per-agent",
        )
        == "agent:coder:main"
    )


def test_binding_table_uses_most_specific_match() -> None:
    table = BindingTable(default_agent_id="default")
    table.add(Binding(agent_id="channel-agent", channel="telegram"))
    table.add(Binding(agent_id="account-agent", channel="telegram", account_id="bot-a"))
    table.add(Binding(agent_id="peer-agent", channel="telegram", account_id="bot-a", peer_id="123"))

    resolved = table.resolve(
        channel="telegram",
        account_id="bot-a",
        peer_id="123",
    )

    assert resolved == "peer-agent"


def test_binding_table_supports_force_route_and_manual_switch() -> None:
    table = BindingTable(default_agent_id="default")
    table.switch_agent(
        agent_id="manual-agent",
        channel="cli",
        account_id="local",
        peer_id="demo",
        force=True,
    )

    assert (
        table.resolve(channel="cli", account_id="local", peer_id="demo")
        == "manual-agent"
    )
    assert (
        table.resolve(
            channel="cli",
            account_id="local",
            peer_id="demo",
            force_agent_id="forced-agent",
        )
        == "forced-agent"
    )
    assert table.clear(channel="cli", account_id="local", peer_id="demo") == 1
    assert table.resolve(channel="cli", account_id="local", peer_id="demo") == "default"


async def test_gateway_handles_inbound_and_sends_reply() -> None:
    channel = CLIChannel(account_id="local", emit=False)
    manager = ChannelManager()
    manager.register(channel)
    table = BindingTable(default_agent_id="default")
    table.add(Binding(agent_id="coder", channel="cli", account_id="local", peer_id="demo"))

    seen: list[tuple[InboundMessage, str, str]] = []

    async def run_agent_turn(inbound: InboundMessage, agent_id: str, session_id: str) -> str:
        seen.append((inbound, agent_id, session_id))
        return f"{agent_id}:{session_id}:{inbound.text}"

    gateway = Gateway(
        channel_manager=manager,
        binding_table=table,
        agent_configs={
            "coder": AgentRouteConfig(
                agent_id="coder",
                dm_scope="per-account-channel-peer",
            )
        },
        run_agent_turn=run_agent_turn,
    )
    inbound = InboundMessage.cli("hello", account_id="local", peer_id="demo")

    result = await gateway.handle_inbound(inbound)

    assert result.agent_id == "coder"
    assert result.session_id == "agent:coder:cli:local:direct:demo"
    assert result.delivered
    assert channel.sent[0].to == "demo"
    assert channel.sent[0].text == "coder:agent:coder:cli:local:direct:demo:hello"
    assert seen[0][1:] == ("coder", "agent:coder:cli:local:direct:demo")


async def test_gateway_receive_once_uses_channel_inbox() -> None:
    channel = CLIChannel(account_id="local", emit=False)
    channel.push("queued", peer_id="demo")
    manager = ChannelManager()
    manager.register(channel)

    async def run_agent_turn(inbound: InboundMessage, agent_id: str, session_id: str) -> str:
        return f"reply to {inbound.text}"

    gateway = Gateway(
        channel_manager=manager,
        binding_table=BindingTable(default_agent_id="default"),
        agent_configs={"default": AgentRouteConfig(agent_id="default")},
        run_agent_turn=run_agent_turn,
    )

    result = await gateway.receive_once("cli")

    assert result is not None
    assert result.reply == "reply to queued"
    assert channel.sent[0].text == "reply to queued"
