from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agent.channels.base import Channel, InboundMessage


class FeishuChannel(Channel):
    name = "feishu"

    def __init__(
        self,
        *,
        app_id: str = "",
        app_secret: str = "",
        account_id: str = "feishu-bot",
        verification_token: str = "",
        encrypt_key: str = "",
        bot_open_id: str = "",
        is_lark: bool = False,
        webhook_host: str = "127.0.0.1",
        webhook_port: int = 0,
        receive_timeout_s: float = 30.0,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.account_id = account_id
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.bot_open_id = bot_open_id
        self.api_base = (
            "https://open.larksuite.com/open-apis"
            if is_lark
            else "https://open.feishu.cn/open-apis"
        )
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.receive_timeout_s = receive_timeout_s
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0
        self._inbox: queue.Queue[InboundMessage] = queue.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    async def receive(self) -> InboundMessage | None:
        self.ensure_webhook_server()
        try:
            return await asyncio.to_thread(
                self._inbox.get,
                True,
                self.receive_timeout_s,
            )
        except queue.Empty:
            return None

    async def send(self, to: str, text: str, **kwargs: Any) -> bool:
        token = await asyncio.to_thread(self._refresh_tenant_token)
        if not token:
            return False

        inbound = kwargs.get("inbound")
        receive_id_type = kwargs.get("receive_id_type") or self._infer_receive_id_type(
            to,
            inbound=inbound if isinstance(inbound, dict) else None,
        )
        payload = {
            "receive_id": to,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        path = f"/im/v1/messages?{urllib.parse.urlencode({'receive_id_type': receive_id_type})}"
        try:
            data = await asyncio.to_thread(
                self._post_json,
                path,
                payload,
                {"Authorization": f"Bearer {token}"},
            )
        except Exception:
            return False
        return data.get("code") == 0

    async def close(self) -> None:
        if self._server is not None:
            await asyncio.to_thread(self._server.shutdown)
            self._server.server_close()
        self._server = None
        self._server_thread = None

    def ensure_webhook_server(self) -> None:
        if self.webhook_port <= 0 or self._server is not None:
            return

        channel = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._write_json({"error": "invalid json"}, status=400)
                    return

                response, status = channel.handle_webhook_payload(payload)
                self._write_json(response, status=status)

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _write_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.webhook_host, self.webhook_port), Handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._server_thread.start()

    def handle_webhook_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if not self._token_valid(payload):
            return {"error": "invalid verification token"}, 401
        challenge = self.challenge_response(payload)
        if challenge is not None:
            return challenge, 200
        if payload.get("encrypt"):
            return {"error": "encrypted events are not supported yet"}, 400

        inbound = self.push_event(payload)
        if inbound is None:
            return {}, 200
        return {}, 200

    def push_event(self, payload: dict[str, Any]) -> InboundMessage | None:
        inbound = self.parse_event(payload)
        if inbound is None:
            return None
        self._inbox.put(inbound)
        return inbound

    def challenge_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._token_valid(payload):
            return None
        challenge = payload.get("challenge")
        if isinstance(challenge, str):
            return {"challenge": challenge}
        return None

    def parse_event(self, payload: dict[str, Any]) -> InboundMessage | None:
        if not self._token_valid(payload):
            return None
        if payload.get("encrypt"):
            return None
        if "challenge" in payload:
            return None

        event = payload.get("event")
        if not isinstance(event, dict):
            return None

        message = event.get("message")
        if not isinstance(message, dict):
            return None

        sender = event.get("sender")
        sender_id = self._sender_id(sender if isinstance(sender, dict) else {})
        chat_id = str(message.get("chat_id") or "")
        chat_type = str(message.get("chat_type") or "")
        is_group = chat_type == "group"

        if is_group and self.bot_open_id and not self._bot_mentioned(message):
            return None

        text, media = self._parse_content(message)
        if not text and not media:
            return None

        peer_id = sender_id if chat_type == "p2p" else chat_id
        if not peer_id:
            return None

        return InboundMessage(
            text=text or "[media]",
            sender_id=sender_id or peer_id,
            channel=self.name,
            account_id=self.account_id,
            peer_id=peer_id,
            guild_id=chat_id if is_group else "",
            is_group=is_group,
            media=media,
            raw=payload,
        )

    def _refresh_tenant_token(self) -> str:
        if self._tenant_token and time.time() < self._tenant_token_expires_at:
            return self._tenant_token
        if not self.app_id or not self.app_secret:
            return ""

        try:
            data = self._post_json(
                "/auth/v3/tenant_access_token/internal",
                {
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
        except Exception:
            return ""
        if data.get("code") != 0:
            return ""

        token = str(data.get("tenant_access_token") or "")
        if not token:
            return ""
        expires_in = int(data.get("expire") or 7200)
        self._tenant_token = token
        self._tenant_token_expires_at = time.time() + max(expires_in - 300, 60)
        return self._tenant_token

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                **(headers or {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _token_valid(self, payload: dict[str, Any]) -> bool:
        if not self.verification_token:
            return True
        token = payload.get("token")
        header = payload.get("header")
        if isinstance(header, dict):
            token = header.get("token", token)
        return token == self.verification_token

    def _sender_id(self, sender: dict[str, Any]) -> str:
        sender_id = sender.get("sender_id")
        if not isinstance(sender_id, dict):
            return ""
        for key in ("open_id", "user_id", "union_id"):
            value = sender_id.get(key)
            if value:
                return str(value)
        return ""

    def _bot_mentioned(self, message: dict[str, Any]) -> bool:
        mentions = message.get("mentions") or []
        if not isinstance(mentions, list):
            return False
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            mention_id = mention.get("id")
            if isinstance(mention_id, dict) and mention_id.get("open_id") == self.bot_open_id:
                return True
            if mention_id == self.bot_open_id:
                return True
            if mention.get("key") == self.bot_open_id:
                return True
        return False

    def _parse_content(self, message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        msg_type = str(message.get("message_type") or message.get("msg_type") or "text")
        raw_content = message.get("content") or "{}"
        try:
            content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        except json.JSONDecodeError:
            return "", []
        if not isinstance(content, dict):
            return "", []

        media: list[dict[str, Any]] = []
        if msg_type == "text":
            return str(content.get("text") or ""), media
        if msg_type == "post":
            return self._parse_post_content(content), media
        if msg_type == "image":
            image_key = str(content.get("image_key") or "")
            if image_key:
                media.append({"type": "image", "key": image_key})
            return "[image]", media
        if msg_type == "file":
            file_key = str(content.get("file_key") or "")
            if file_key:
                media.append({"type": "file", "key": file_key})
            return "[file]", media
        return "", media

    def _parse_post_content(self, content: dict[str, Any]) -> str:
        texts: list[str] = []
        for localized in content.values():
            if not isinstance(localized, dict):
                continue
            title = localized.get("title")
            if title:
                texts.append(str(title))
            paragraphs = localized.get("content")
            if not isinstance(paragraphs, list):
                continue
            for paragraph in paragraphs:
                if not isinstance(paragraph, list):
                    continue
                for node in paragraph:
                    if not isinstance(node, dict):
                        continue
                    tag = node.get("tag")
                    if tag == "text":
                        texts.append(str(node.get("text") or ""))
                    elif tag == "a":
                        text = str(node.get("text") or "")
                        href = str(node.get("href") or "")
                        texts.append(f"{text} {href}".strip())
        return "\n".join(text for text in texts if text)

    def _infer_receive_id_type(self, to: str, *, inbound: dict[str, Any] | None) -> str:
        if inbound and inbound.get("is_group"):
            return "chat_id"
        if to.startswith("oc_"):
            return "chat_id"
        if to.startswith("ou_"):
            return "open_id"
        return "open_id"
