from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    sender_id: str
    channel: str
    account_id: str
    peer_id: str
    guild_id: str = ""
    is_group: bool = False
    media: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def cli(
        cls,
        text: str,
        *,
        account_id: str = "local",
        peer_id: str = "default",
        sender_id: str = "local-user",
    ) -> "InboundMessage":
        return cls(
            text=text,
            sender_id=sender_id,
            channel="cli",
            account_id=account_id,
            peer_id=peer_id,
        )


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str
    text: str
    channel: str
    account_id: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class Channel(ABC):
    name: str = "unknown"

    @abstractmethod
    async def receive(self) -> InboundMessage | None:
        raise NotImplementedError

    @abstractmethod
    async def send(self, to: str, text: str, **kwargs: Any) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class ChannelManager:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        if channel.name in self._channels:
            raise ValueError(f"channel already registered: {channel.name}")
        self._channels[channel.name] = channel

    def get(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError as exc:
            raise KeyError(f"unknown channel: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._channels)
