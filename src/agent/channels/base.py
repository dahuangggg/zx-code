"""channels.base — 消息通道抽象基类与核心数据模型。

``InboundMessage`` 是通道无关的消息载体，包含：
  text       — 消息文本内容
  channel    — 来源通道名称（"cli" / "telegram" / "feishu"）
  account_id — 机器人账号标识（同一通道可有多个账号）
  peer_id    — 发送者 ID（用户/群组唯一标识）
  guild_id   — 群组 ID（私信为空）

``ChannelManager`` 管理多个通道适配器，按名称路由消息。
新增通道只需实现 ChannelAdapter 接口并注册到 ChannelManager。
"""
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
