from __future__ import annotations

from pathlib import Path

from agent.channels import ChannelManager, CLIChannel
from agent.channels.telegram import TelegramChannel


async def test_cli_channel_receives_and_sends() -> None:
    outputs: list[str] = []
    channel = CLIChannel(account_id="local", emit=True, writer=outputs.append)
    channel.push("hello", peer_id="demo")

    inbound = await channel.receive()
    sent = await channel.send("demo", "reply", session_id="s1")

    assert inbound is not None
    assert inbound.channel == "cli"
    assert inbound.account_id == "local"
    assert inbound.peer_id == "demo"
    assert sent
    assert outputs == ["reply"]
    assert channel.sent[0].raw["session_id"] == "s1"


def test_channel_manager_registers_and_gets_channels() -> None:
    manager = ChannelManager()
    channel = CLIChannel()

    manager.register(channel)

    assert manager.get("cli") is channel
    assert manager.names() == ["cli"]


def test_telegram_channel_normalizes_private_update() -> None:
    update = {
        "update_id": 10,
        "message": {
            "text": "hello from phone",
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
        },
    }

    inbound = TelegramChannel.inbound_from_update(update, account_id="bot-a")

    assert inbound is not None
    assert inbound.channel == "telegram"
    assert inbound.account_id == "bot-a"
    assert inbound.sender_id == "456"
    assert inbound.peer_id == "456"
    assert inbound.guild_id == ""
    assert not inbound.is_group


def test_telegram_channel_normalizes_group_update() -> None:
    update = {
        "update_id": 10,
        "message": {
            "text": "/agent status",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 456},
        },
    }

    inbound = TelegramChannel.inbound_from_update(update)

    assert inbound is not None
    assert inbound.peer_id == "-100"
    assert inbound.guild_id == "-100"
    assert inbound.is_group


def test_telegram_channel_normalizes_forum_topic_update() -> None:
    update = {
        "update_id": 10,
        "message": {
            "text": "topic message",
            "message_thread_id": 7,
            "chat": {"id": -100, "type": "supergroup", "is_forum": True},
            "from": {"id": 456},
        },
    }

    inbound = TelegramChannel.inbound_from_update(update)

    assert inbound is not None
    assert inbound.peer_id == "-100:topic:7"
    assert inbound.guild_id == "-100"
    assert inbound.is_group


async def test_telegram_channel_persists_offset_and_filters_allowed_chats(tmp_path: Path) -> None:
    channel = TelegramChannel(
        token="token",
        account_id="bot-a",
        state_dir=tmp_path,
        allowed_chats={"456"},
        text_coalesce_s=0,
    )
    calls: list[tuple[str, dict]] = []

    def fake_api(method: str, payload: dict):
        calls.append((method, payload))
        return [
            {
                "update_id": 10,
                "message": {
                    "text": "hello",
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 456},
                },
            },
            {
                "update_id": 11,
                "message": {
                    "text": "blocked",
                    "chat": {"id": 999, "type": "private"},
                    "from": {"id": 999},
                },
            },
        ]

    channel._api = fake_api  # type: ignore[method-assign]

    inbound = await channel.receive()

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.peer_id == "456"
    assert calls[0][0] == "getUpdates"
    assert (tmp_path / "telegram" / "offset-bot-a.txt").read_text(encoding="utf-8") == "12"


async def test_telegram_channel_coalesces_text_without_second_long_poll(tmp_path: Path) -> None:
    channel = TelegramChannel(
        token="token",
        account_id="bot-a",
        state_dir=tmp_path,
        text_coalesce_s=0.01,
    )
    calls = 0

    def fake_api(method: str, payload: dict):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("receive should flush buffered text before polling again")
        return [
            {
                "update_id": 10,
                "message": {
                    "text": "hello",
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 456},
                },
            }
        ]

    channel._api = fake_api  # type: ignore[method-assign]

    inbound = await channel.receive()

    assert inbound is not None
    assert inbound.text == "hello"
    assert calls == 1


async def test_telegram_channel_sends_topic_chunks() -> None:
    channel = TelegramChannel(token="token", text_coalesce_s=0)
    sent: list[tuple[str, dict]] = []

    def fake_api(method: str, payload: dict):
        sent.append((method, payload))
        return {"message_id": len(sent)}

    channel._api = fake_api  # type: ignore[method-assign]
    channel.max_message_len = 5

    ok = await channel.send("-100:topic:7", "hello\nworld")

    assert ok
    assert sent == [
        ("sendMessage", {"chat_id": "-100", "text": "hello", "message_thread_id": 7}),
        ("sendMessage", {"chat_id": "-100", "text": "world", "message_thread_id": 7}),
    ]
