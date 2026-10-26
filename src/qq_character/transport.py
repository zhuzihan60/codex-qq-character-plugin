"""Scoped text-only OneBot transport, extending the pinned MIT companion bridge.

The upstream owns the WebSocket lifecycle and action correlation. This module
owns identity, recipient policy, durable event ingestion and delivery semantics.
It deliberately imports neither the upstream Codex client nor its entrypoints.
"""
from __future__ import annotations

import asyncio
import hmac
import inspect
import ipaddress
import logging
import math
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any
from pathlib import Path
from urllib.parse import urlsplit

# Source/editable installations only: keep the exact inspected MIT bridge.
# Do not import the upstream CLI, Codex runtime, image handling or skill loader.
_VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "qq-codex-companion" / "src"
if not (_VENDOR / "qq_codex_companion" / "onebot.py").is_file():
    raise RuntimeError("Pinned OneBot source is missing; install this checkout in editable mode")
sys.path.insert(0, str(_VENDOR))
from qq_codex_companion.config import OneBotSettings
from qq_codex_companion.onebot import OneBotReverseServer

LOGGER = logging.getLogger(__name__)
_QQ_ID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)
_MESSAGE_ID = re.compile(r"-?(?:0|[1-9][0-9]{0,29})\Z", re.ASCII)
_SCOPE = re.compile(r"onebot:([1-9][0-9]{0,19}):(private|group):([1-9][0-9]{0,19})\Z", re.ASCII)
_CQ = re.compile(r"\[CQ:[^\]]*\]", re.IGNORECASE)
MAX_INPUT_CHARS = 8000
MAX_OUTPUT_CHARS = 4000


class NotConnected(RuntimeError):
    """Nothing was submitted; retry after reconnect is safe."""


class AmbiguousDelivery(RuntimeError):
    """Submission may have happened; reconcile manually, never blind-retry."""


def _identifier(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("QQ identifier must be a positive decimal integer")
    result = str(value)
    if not _QQ_ID.fullmatch(result):
        raise ValueError("QQ identifier must use canonical positive decimal notation")
    return result


def _message_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("OneBot message_id is required")
    result = str(value)
    if not _MESSAGE_ID.fullmatch(result) or result != str(int(result)):
        raise ValueError("OneBot message_id must be a decimal integer")
    return result


class OneBotTransport(OneBotReverseServer):
    """Private allowlist + explicitly mentioned allowlisted groups.

    ``store.ingest`` must synchronously commit and deduplicate by event_id.
    ``on_event`` is an optional zero-argument worker wakeup, never a model call.
    Events are persisted in socket arrival order; occurrence time is retained
    separately so delayed/replayed events do not pretend to have happened now.
    """

    def __init__(self, config: dict, store: Any, on_event: Callable | None = None) -> None:
        cfg = config.get("transport", {})
        self.bot_id = _identifier(cfg.get("bot_id"))
        self.allowed_private = frozenset(_identifier(v) for v in cfg.get("allowed_private", []))
        self.allowed_groups = frozenset(_identifier(v) for v in cfg.get("allowed_groups", []))
        if self.bot_id in self.allowed_private:
            raise ValueError("The bot cannot be its own private recipient")
        token_env = cfg.get("token_env", "QQ_CHARACTER_ONEBOT_TOKEN")
        if not isinstance(token_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ValueError("transport.token_env must name an environment variable")
        token = os.environ.get(token_env, "")
        if len(token) < 24 or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError(f"Set {token_env} to an independent ASCII token of at least 24 characters")
        host = cfg.get("host", "127.0.0.1")
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("This implementation listens on loopback only; use an authenticated tunnel for remote NapCat")
        port = cfg.get("port", 6198)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("transport.port must be an integer in 1..65535")
        path = cfg.get("path", "/onebot")
        if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9/_-]+", path):
            raise ValueError("transport.path must be a literal URL path")
        self.store = store
        self.on_event = on_event
        self._ingest_lock = asyncio.Lock()
        self.autonomous_scopes = frozenset(config.get("autonomy", {}).get("scopes", []))
        for scope in self.autonomous_scopes:
            self._scope_recipient(scope)
        settings = OneBotSettings(host, port, path, token, int(self.bot_id),
                                  frozenset(int(v) for v in self.allowed_private))
        super().__init__(settings, self._unused_message_handler)

    async def _unused_message_handler(self, *_args: Any) -> None:
        raise RuntimeError("Only the persistent event path is supported")

    def _authorized(self, connection: Any) -> bool:
        # OneBot v11 Universal reverse WS headers bind both events and API calls
        # to the configured bot. Tokens in URLs are intentionally not accepted.
        try:
            request = connection.request
            parsed = urlsplit(request.path)
            if (parsed.path != self.settings.path or parsed.query or parsed.fragment
                    or parsed.scheme or parsed.netloc):
                return False
            headers = request.headers
            if _identifier(headers.get("X-Self-ID")) != self.bot_id:
                return False
            if headers.get("X-Client-Role", "").lower() != "universal":
                return False
            header = headers.get("Authorization", "")
            if not isinstance(header, str) or not header.startswith("Bearer "):
                return False
            supplied = header[7:]
            return supplied.isascii() and hmac.compare_digest(supplied, self.settings.access_token)
        except (AttributeError, TypeError, ValueError, LookupError):
            # Includes duplicate-header errors from multidict implementations.
            return False

    def _scope_recipient(self, scope: Any) -> tuple[str, str]:
        match = _SCOPE.fullmatch(scope) if isinstance(scope, str) else None
        if not match or match[1] != self.bot_id:
            raise PermissionError("Invalid or cross-account conversation scope")
        kind, recipient = match[2], match[3]
        allowed = self.allowed_private if kind == "private" else self.allowed_groups
        if recipient not in allowed:
            raise PermissionError("Recipient is outside the configured allowlist")
        return kind, recipient

    def can_autonomous(self, scope: str) -> bool:
        try:
            self._scope_recipient(scope)
        except PermissionError:
            return False
        return scope in self.autonomous_scopes

    def _content(self, message: Any, group: bool) -> str | None:
        if isinstance(message, str):
            # A textual CQ string cannot prove an actual group mention.
            if group:
                return None
            text = _CQ.sub("[非文本内容未读取]", message)
        elif isinstance(message, list):
            if len(message) > 128:
                return None
            parts: list[str] = []
            mentioned = False
            for segment in message:
                if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                    return None
                kind, data = segment.get("type"), segment["data"]
                if kind == "text":
                    if not isinstance(data.get("text"), str):
                        return None
                    parts.append(data["text"])
                elif kind == "at":
                    if str(data.get("qq")) == self.bot_id:
                        mentioned = True
                    else:
                        parts.append("[提及其他成员]")
                else:
                    # Never request local image paths, attachment URLs, or quoted
                    # messages from another scope. Metadata is not read content.
                    parts.append("[非文本内容未读取]")
            if group and not mentioned:
                return None
            text = "".join(parts)
        else:
            return None
        text = text.replace("\x00", "").strip()
        if not text:
            return None
        if len(text) > MAX_INPUT_CHARS:
            suffix = "\n[消息过长，后文未读取]"
            text = text[:MAX_INPUT_CHARS - len(suffix)] + suffix
        return text

    async def _handle_event(self, event: dict[str, Any]) -> None:
        if (not isinstance(event, dict) or event.get("post_type") != "message"
                or not isinstance(event.get("message_type"), str)
                or event["message_type"] not in {"private", "group"}):
            return
        try:
            if _identifier(event.get("self_id")) != self.bot_id:
                return
            sender = _identifier(event.get("user_id"))
            if sender == self.bot_id:
                return
            sender_data = event.get("sender")
            if isinstance(sender_data, dict) and "user_id" in sender_data:
                if _identifier(sender_data["user_id"]) != sender:
                    return
            kind = event["message_type"]
            chat = sender if kind == "private" else _identifier(event.get("group_id"))
            scope = f"onebot:{self.bot_id}:{kind}:{chat}"
            self._scope_recipient(scope)
            event_id = f"{scope}:{_message_id(event.get('message_id'))}"
            occurred = event.get("time")
            if isinstance(occurred, bool) or not isinstance(occurred, (int, float)):
                return
            occurred = float(occurred)
            if not math.isfinite(occurred) or occurred <= 0 or occurred > time.time() + 60:
                return
            text = self._content(event.get("message"), group=kind == "group")
            if text is None:
                return
        except (ValueError, TypeError, PermissionError):
            return
        async with self._ingest_lock:
            accepted = self.store.ingest(event_id, scope, "message", text, sender, occurred)
        if accepted and self.on_event:
            try:
                result = self.on_event()
            except Exception:
                LOGGER.error("Worker wakeup failed; the event remains durably queued")
                return
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                self._tasks.add(task)
                task.add_done_callback(self._task_done)

    async def _call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        # Also guard upstream convenience methods (images/pokes/get_image) so
        # they cannot accidentally bypass the intentionally narrow surface.
        if action not in {"send_private_msg", "send_group_msg"}:
            raise PermissionError("Only scoped text sending is enabled")
        kind = "private" if action == "send_private_msg" else "group"
        key = "user_id" if kind == "private" else "group_id"
        recipient = _identifier(params.get(key))
        self._scope_recipient(f"onebot:{self.bot_id}:{kind}:{recipient}")
        message = params.get("message")
        if (set(params) != {key, "message"} or not isinstance(message, list) or len(message) != 1
                or not isinstance(message[0], dict) or set(message[0]) != {"type", "data"}
                or message[0].get("type") != "text" or not isinstance(message[0].get("data"), dict)
                or set(message[0]["data"]) != {"text"}
                or not isinstance(message[0]["data"]["text"], str)
                or not 1 <= len(message[0]["data"]["text"]) <= MAX_OUTPUT_CHARS):
            raise PermissionError("Only one bounded text segment may be sent")
        return await super()._call_action(action, params)

    async def send(self, scope: str, text: str) -> dict[str, Any]:
        kind, recipient = self._scope_recipient(scope)
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_OUTPUT_CHARS or "\x00" in text:
            raise ValueError(f"Reply must contain 1..{MAX_OUTPUT_CHARS} characters of text")
        if self._connection is None:
            raise NotConnected("OneBot is disconnected; nothing was submitted")
        key = "user_id" if kind == "private" else "group_id"
        try:
            response = await self._call_action(f"send_{kind}_msg", {
                key: int(recipient), "message": [{"type": "text", "data": {"text": text}}],
            })
            data = response.get("data")
            if (response.get("status") != "ok" or type(response.get("retcode")) is not int
                    or response["retcode"] != 0 or not isinstance(data, dict)):
                raise ValueError("No definite successful send acknowledgement")
            message_id = _message_id(data.get("message_id"))
            return {"transport": "onebot", "status": "ok", "message_id": message_id}
        except asyncio.CancelledError:
            # The durable outbox must mark inflight entries unknown on restart.
            raise
        except Exception as error:
            # Upstream turns unsuccessful acks into OneBotError strings. Do not
            # guess retry_after or rejection semantics by parsing those strings.
            raise AmbiguousDelivery("Send was attempted without a confirmed receipt; do not automatically retry") from error

    async def send_private_message(self, user_id: int, message: str) -> dict[str, Any]:
        return await self.send(f"onebot:{self.bot_id}:private:{_identifier(user_id)}", message)
