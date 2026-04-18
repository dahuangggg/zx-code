from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from agent.channels.base import Channel, InboundMessage, OutboundMessage


class CLIChannel(Channel):
    name = "cli"

    def __init__(
        self,
        *,
        account_id: str = "local",
        sender_id: str = "local-user",
        emit: bool = True,
        writer: Callable[[str], None] | None = None,
    ) -> None:
        self.account_id = account_id
        self.sender_id = sender_id
        self.emit = emit
        self.writer = writer or print
        self._inbox: deque[InboundMessage] = deque()
        self.sent: list[OutboundMessage] = []

    def push(self, text: str, *, peer_id: str = "default") -> InboundMessage:
        inbound = InboundMessage.cli(
            text,
            account_id=self.account_id,
            peer_id=peer_id,
            sender_id=self.sender_id,
        )
        self._inbox.append(inbound)
        return inbound

    async def receive(self) -> InboundMessage | None:
        if not self._inbox:
            return None
        return self._inbox.popleft()

    async def send(self, to: str, text: str, **kwargs: Any) -> bool:
        self.sent.append(
            OutboundMessage(
                to=to,
                text=text,
                channel=self.name,
                account_id=self.account_id,
                raw=dict(kwargs),
            )
        )
        if self.emit:
            self.writer(text)
        return True

