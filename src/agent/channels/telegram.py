from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

from agent.channels.base import Channel, InboundMessage


class TelegramChannel(Channel):
    name = "telegram"
    max_message_len = 4096
    max_buffer_size = 1000

    def __init__(
        self,
        *,
        token: str,
        account_id: str = "telegram-bot",
        offset: int | None = None,
        timeout_s: int = 30,
        state_dir: str | Path = ".agent/channels",
        allowed_chats: str | set[str] = "",
        text_coalesce_s: float = 1.0,
        media_group_coalesce_s: float = 0.5,
    ) -> None:
        self.token = token
        self.account_id = account_id
        self.timeout_s = timeout_s
        self.text_coalesce_s = text_coalesce_s
        self.media_group_coalesce_s = media_group_coalesce_s
        self._offset_path = Path(state_dir) / "telegram" / f"offset-{self.account_id}.txt"
        self.offset = offset if offset is not None else self._load_offset()
        if isinstance(allowed_chats, str):
            self.allowed_chats = {
                item.strip()
                for item in allowed_chats.split(",")
                if item.strip()
            }
        else:
            self.allowed_chats = set(allowed_chats)
        self._ready: deque[InboundMessage] = deque()
        self._seen_updates: set[int] = set()
        self._media_groups: dict[str, dict[str, Any]] = {}
        self._text_buffer: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def receive(self) -> InboundMessage | None:
        if self._ready:
            return self._ready.popleft()

        deadline: float | None = None
        while True:
            if not self._has_pending_buffers():
                messages = await self.poll()
                self._ready.extend(messages)
                if self._ready:
                    return self._ready.popleft()
                if not self._has_pending_buffers():
                    return None

            if self._ready:
                return self._ready.popleft()
            if not self._has_pending_buffers():
                return None
            if deadline is None:
                deadline = time.monotonic() + max(
                    self.text_coalesce_s,
                    self.media_group_coalesce_s,
                    0,
                )
            if time.monotonic() >= deadline:
                self._ready.extend(self._flush_buffers())
                if self._ready:
                    return self._ready.popleft()
                return None
            await asyncio.sleep(0.05)

    async def poll(self) -> list[InboundMessage]:
        params: dict[str, Any] = {
            "offset": self.offset,
            "timeout": self.timeout_s,
            "allowed_updates": ["message", "edited_message"],
        }
        updates = await asyncio.to_thread(self._api, "getUpdates", params)
        if not isinstance(updates, list):
            return self._flush_buffers()

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                if update_id in self._seen_updates:
                    continue
                self._seen_updates.add(update_id)
                if len(self._seen_updates) > 5000:
                    self._seen_updates.clear()
                if self.offset is None or update_id >= self.offset:
                    self.offset = update_id + 1
                    self._save_offset(self.offset)

            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                continue
            if message.get("media_group_id"):
                self._buffer_media(message, update)
                continue

            inbound = self.inbound_from_update(update, account_id=self.account_id)
            if inbound is None or not self._chat_allowed(inbound):
                continue
            self._buffer_text(inbound)

        return self._flush_buffers()

    async def send(self, to: str, text: str, **kwargs: Any) -> bool:
        chat_id, thread_id = self._split_topic_target(to)
        ok = True
        for chunk in self._chunk(text):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            sent = await asyncio.to_thread(self._api, "sendMessage", payload)
            if not sent:
                ok = False
        return ok

    async def send_typing(self, to: str) -> bool:
        chat_id, thread_id = self._split_topic_target(to)
        payload: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        result = await asyncio.to_thread(self._api, "sendChatAction", payload)
        return bool(result)

    @classmethod
    def inbound_from_update(
        cls,
        update: dict[str, Any],
        *,
        account_id: str = "telegram-bot",
    ) -> InboundMessage | None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text") or message.get("caption") or ""
        media = cls.media_from_message(message)
        if not text and not media:
            return None

        chat_id = str(chat.get("id", ""))
        chat_type = str(chat.get("type", "private"))
        is_group = chat_type in {"group", "supergroup"}
        sender_id = str(sender.get("id") or chat_id)
        thread_id = message.get("message_thread_id")

        if chat_type == "private":
            peer_id = sender_id
            guild_id = ""
        elif is_group and chat.get("is_forum") and thread_id is not None:
            peer_id = f"{chat_id}:topic:{thread_id}"
            guild_id = chat_id
        else:
            peer_id = chat_id
            guild_id = chat_id if is_group else ""

        return InboundMessage(
            text=text or "[media]",
            sender_id=sender_id,
            channel=cls.name,
            account_id=account_id,
            peer_id=peer_id,
            guild_id=guild_id,
            is_group=is_group,
            media=media,
            raw=update,
        )

    @classmethod
    def media_from_message(cls, message: dict[str, Any]) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        for media_type in ("photo", "video", "document", "audio", "voice"):
            if media_type not in message:
                continue
            raw_media = message[media_type]
            if isinstance(raw_media, list) and raw_media:
                file_id = raw_media[-1].get("file_id", "")
            elif isinstance(raw_media, dict):
                file_id = raw_media.get("file_id", "")
            else:
                file_id = ""
            if file_id:
                media.append({"type": media_type, "file_id": file_id})
        return media

    def _api(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            data = self._post_json(method, payload)
        except Exception:
            return None
        if not data.get("ok"):
            return None
        return data.get("result")

    def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        filtered = {key: value for key, value in payload.items() if value is not None}
        body = json.dumps(filtered).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s + 5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _buffer_media(self, message: dict[str, Any], update: dict[str, Any]) -> None:
        media_group_id = str(message["media_group_id"])
        if len(self._media_groups) >= self.max_buffer_size:
            self._ready.extend(self._flush_media())
        if media_group_id not in self._media_groups:
            self._media_groups[media_group_id] = {
                "updated_at": time.monotonic(),
                "entries": [],
            }
        group = self._media_groups[media_group_id]
        group["updated_at"] = time.monotonic()
        group["entries"].append((message, update))

    def _flush_media(self) -> list[InboundMessage]:
        ready: list[InboundMessage] = []
        now = time.monotonic()
        expired = [
            group_id
            for group_id, group in self._media_groups.items()
            if now - group["updated_at"] >= self.media_group_coalesce_s
        ]
        for group_id in expired:
            entries = self._media_groups.pop(group_id)["entries"]
            if not entries:
                continue
            first_message, first_update = entries[0]
            inbound = self.inbound_from_update(first_update, account_id=self.account_id)
            if inbound is None:
                continue
            captions: list[str] = []
            media: list[dict[str, Any]] = []
            for message, _update in entries:
                caption = message.get("caption")
                if caption:
                    captions.append(caption)
                media.extend(self.media_from_message(message))
            inbound.text = "\n".join(captions) if captions else "[media group]"
            inbound.media = media
            if self._chat_allowed(inbound):
                ready.append(inbound)
        return ready

    def _buffer_text(self, inbound: InboundMessage) -> None:
        if self.text_coalesce_s <= 0:
            self._ready.append(inbound)
            return
        if len(self._text_buffer) >= self.max_buffer_size:
            self._ready.extend(self._flush_text())
        key = (inbound.peer_id, inbound.sender_id)
        now = time.monotonic()
        if key in self._text_buffer:
            self._text_buffer[key]["text"] += "\n" + inbound.text
            self._text_buffer[key]["updated_at"] = now
            self._text_buffer[key]["message"] = inbound
            return
        self._text_buffer[key] = {
            "text": inbound.text,
            "updated_at": now,
            "message": inbound,
        }

    def _flush_text(self) -> list[InboundMessage]:
        ready: list[InboundMessage] = []
        now = time.monotonic()
        expired = [
            key
            for key, buffered in self._text_buffer.items()
            if now - buffered["updated_at"] >= self.text_coalesce_s
        ]
        for key in expired:
            buffered = self._text_buffer.pop(key)
            inbound = buffered["message"]
            inbound.text = buffered["text"]
            ready.append(inbound)
        return ready

    def _flush_buffers(self) -> list[InboundMessage]:
        ready = self._flush_media()
        ready.extend(self._flush_text())
        return ready

    def _has_pending_buffers(self) -> bool:
        return bool(self._media_groups or self._text_buffer)

    def _chat_allowed(self, inbound: InboundMessage) -> bool:
        if not self.allowed_chats:
            return True
        return inbound.peer_id in self.allowed_chats or inbound.guild_id in self.allowed_chats

    def _chunk(self, text: str) -> list[str]:
        if len(text) <= self.max_message_len:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= self.max_message_len:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, self.max_message_len)
            if cut <= 0:
                cut = self.max_message_len
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip("\n")
        return chunks

    def _split_topic_target(self, to: str) -> tuple[str, int | None]:
        if ":topic:" not in to:
            return to, None
        chat_id, raw_thread_id = to.split(":topic:", 1)
        try:
            return chat_id, int(raw_thread_id)
        except ValueError:
            return chat_id, None

    def _load_offset(self) -> int | None:
        try:
            return int(self._offset_path.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def _save_offset(self, offset: int) -> None:
        self._offset_path.parent.mkdir(parents=True, exist_ok=True)
        self._offset_path.write_text(str(offset), encoding="utf-8")
