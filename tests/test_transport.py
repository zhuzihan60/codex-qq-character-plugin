"""Transport policy and upstream correlation tests; no real QQ connection."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from qq_character.transport import AmbiguousDelivery, NotConnected, OneBotTransport


CONFIG = {
    "transport": {
        "bot_id": "10001", "allowed_private": ["20002"], "allowed_groups": ["30003"],
        "token_env": "TEST_QQ_CHARACTER_TOKEN", "host": "127.0.0.1", "port": 6198, "path": "/onebot",
    },
    "autonomy": {"scopes": ["onebot:10001:private:20002"]},
}
TOKEN = "test-only-not-a-real-token-123456789"


class MemoryStore:
    """A deliberately mocked store: core tests own SQLite durability checks."""

    def __init__(self):
        self.rows = {}

    def ingest(self, event_id, scope, kind, text, sender, occurred, now=None):
        if event_id in self.rows:
            return False
        self.rows[event_id] = dict(scope=scope, kind=kind, text=text, sender=sender, occurred=occurred)
        return True


def event(**overrides):
    value = {
        "post_type": "message", "message_type": "private", "self_id": 10001,
        "user_id": 20002, "message_id": 41, "time": time.time() - 1,
        "message": [{"type": "text", "data": {"text": "记得上次一起讨论潮汐吗"}}],
    }
    value.update(overrides)
    return value


class AckConnection:
    """Simulates a socket and correlated acknowledgement, never QQ delivery."""

    def __init__(self, transport, response=None, error=None):
        self.transport = transport
        self.response = response if response is not None else {"status": "ok", "retcode": 0, "data": {"message_id": 123}}
        self.error = error
        self.sent = []

    async def send(self, frame):
        payload = json.loads(frame)
        self.sent.append(payload)
        if self.error:
            raise self.error
        self.transport._pending_actions[payload["echo"]].set_result(self.response)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TEST_QQ_CHARACTER_TOKEN": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = MemoryStore()
        self.wakeups = 0

        def wakeup():
            self.wakeups += 1

        self.transport = OneBotTransport(copy.deepcopy(CONFIG), self.store, wakeup)

    def request(self, path="/onebot", **overrides):
        headers = {"Authorization": "Bearer " + TOKEN, "X-Self-ID": "10001", "X-Client-Role": "Universal"}
        headers.update(overrides)
        return SimpleNamespace(request=SimpleNamespace(path=path, headers=headers))

    def test_header_auth_is_bound_to_bot_role_and_exact_path(self):
        self.assertTrue(self.transport._authorized(self.request()))
        for connection in [
            self.request("/"), self.request("/onebot?access_token=" + TOKEN),
            self.request(**{"Authorization": ""}), self.request(**{"Authorization": "Bearer wrong"}),
            self.request(**{"X-Self-ID": "10002"}), self.request(**{"X-Client-Role": "Event"}),
            self.request(**{"X-Self-ID": None}), self.request(**{"Authorization": "Bearer 非ASCII"}),
        ]:
            self.assertFalse(self.transport._authorized(connection))

    def test_configuration_rejects_weak_token_and_public_listener(self):
        with patch.dict(os.environ, {"TEST_QQ_CHARACTER_TOKEN": "short"}):
            with self.assertRaises(ValueError):
                OneBotTransport(CONFIG, self.store)
        cfg = copy.deepcopy(CONFIG)
        cfg["transport"]["host"] = "0.0.0.0"
        with self.assertRaises(ValueError):
            OneBotTransport(cfg, self.store)
        cfg = copy.deepcopy(CONFIG)
        cfg["autonomy"]["scopes"] = ["onebot:10001:private:99999"]
        with self.assertRaises(PermissionError):
            OneBotTransport(cfg, self.store)

    async def test_accepted_events_keep_sender_time_and_arrival_order(self):
        await self.transport._handle_event(event(message_id=-41, time=10))
        await self.transport._handle_event(event(message_id=42, time=9))
        self.assertEqual(list(self.store.rows), ["onebot:10001:private:20002:-41", "onebot:10001:private:20002:42"])
        first = next(iter(self.store.rows.values()))
        self.assertEqual(first["sender"], "20002")
        self.assertEqual(first["occurred"], 10)
        self.assertEqual(self.wakeups, 2)

    async def test_new_transport_uses_store_dedup_not_process_seen_set(self):
        await self.transport._handle_event(event())
        second = OneBotTransport(CONFIG, self.store)
        await second._handle_event(event())
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(self.wakeups, 1)

    async def test_wrong_bot_sender_and_invalid_timestamps_are_ignored(self):
        for item in [
            event(self_id=10002), event(user_id=99999), event(user_id=10001),
            event(sender={"user_id": 99999}), event(time=float("nan")), event(time=True),
            event(time=time.time() + 300), event(message_id=None), event(time=0),
            event(post_type="notice"), event(self_id="010001"), event(user_id=True), event(message_type=[]),
        ]:
            await self.transport._handle_event(item)
        self.assertFalse(self.store.rows)

    async def test_group_requires_genuine_at_segment_and_group_allowlist(self):
        body = [{"type": "text", "data": {"text": "[CQ:at,qq=10001] hello"}}]
        await self.transport._handle_event(event(message_type="group", group_id=30003, message=body))
        await self.transport._handle_event(event(message_type="group", group_id=30003, message="[CQ:at,qq=10001] hello"))
        self.assertFalse(self.store.rows)
        body.insert(0, {"type": "at", "data": {"qq": "10001"}})
        await self.transport._handle_event(event(message_type="group", group_id=99999, message=body))
        self.assertFalse(self.store.rows)
        await self.transport._handle_event(event(message_type="group", group_id=30003, user_id=40004, message=body))
        self.assertEqual(list(self.store.rows), ["onebot:10001:group:30003:41"])
        self.assertEqual(next(iter(self.store.rows.values()))["sender"], "40004")

    async def test_equal_message_ids_in_different_scopes_do_not_collide(self):
        await self.transport._handle_event(event())
        await self.transport._handle_event(event(message_type="group", group_id=30003, message=[
            {"type": "at", "data": {"qq": "10001"}}, {"type": "text", "data": {"text": "你好"}},
        ]))
        self.assertEqual(len(self.store.rows), 2)

    async def test_attachments_are_placeholders_and_never_read(self):
        await self.transport._handle_event(event(message=[
            {"type": "image", "data": {"file": "C:/private/password.txt", "url": "http://127.0.0.1/admin"}},
            {"type": "reply", "data": {"id": "private-other-user"}},
        ]))
        text = next(iter(self.store.rows.values()))["text"]
        self.assertEqual(text, "[非文本内容未读取]" * 2)
        self.assertNotIn("private", text)
        self.assertFalse(self.transport._pending_actions)

    async def test_private_cq_string_is_sanitized(self):
        await self.transport._handle_event(event(message="你好[CQ:image,file=file:///secret]"))
        self.assertEqual(next(iter(self.store.rows.values()))["text"], "你好[非文本内容未读取]")

    async def test_long_input_truncation_stays_inside_store_limit(self):
        await self.transport._handle_event(event(message="长" * 9000))
        text = next(iter(self.store.rows.values()))["text"]
        self.assertEqual(len(text), 8000)
        self.assertTrue(text.endswith("[消息过长，后文未读取]"))

    async def test_async_wakeup_does_not_block_socket_ack_processing(self):
        gate = asyncio.Event()

        async def callback():
            await gate.wait()

        self.transport.on_event = callback
        await asyncio.wait_for(self.transport._handle_event(event()), timeout=1)
        self.assertEqual(len(self.transport._tasks), 1)
        gate.set()
        await asyncio.gather(*self.transport._tasks)

    async def test_private_and_group_sends_use_text_segments_and_receipts(self):
        connection = AckConnection(self.transport)
        self.transport._connection = connection
        dangerous_looking_text = "[CQ:at,qq=all] 这只是文字"
        receipt = await self.transport.send("onebot:10001:private:20002", dangerous_looking_text)
        self.assertEqual(receipt["message_id"], "123")
        self.assertEqual(connection.sent[0]["params"], {
            "user_id": 20002, "message": [{"type": "text", "data": {"text": dangerous_looking_text}}],
        })
        await self.transport.send("onebot:10001:group:30003", "晚上好")
        self.assertEqual(connection.sent[1]["action"], "send_group_msg")
        self.assertFalse(self.transport._pending_actions)

    async def test_send_scope_checked_even_when_not_connected(self):
        for scope in ["onebot:10002:private:20002", "onebot:10001:private:99999",
                      "onebot:10001:group:99999", "onebot:10001:private:20002:extra", "private:20002"]:
            with self.assertRaises(PermissionError):
                await self.transport.send(scope, "不能发送")
        with self.assertRaises(NotConnected):
            await self.transport.send("onebot:10001:private:20002", "稍后发送")

    async def test_attempted_send_errors_and_missing_receipts_are_ambiguous(self):
        cases = [
            AckConnection(self.transport, error=TimeoutError()),
            AckConnection(self.transport, response={"status": "failed", "retcode": 429, "message": "rate limited"}),
            AckConnection(self.transport, response={"status": "ok", "retcode": 0, "data": {}}),
        ]
        for connection in cases:
            self.transport._connection = connection
            with self.assertRaises(AmbiguousDelivery):
                await self.transport.send("onebot:10001:private:20002", "一次提交")
            self.assertEqual(len(connection.sent), 1)
            self.assertFalse(self.transport._pending_actions)

    async def test_inherited_methods_cannot_bypass_scope_or_text_only_policy(self):
        with self.assertRaises(PermissionError):
            await self.transport.send_private_message(99999, "blocked")
        with self.assertRaises(PermissionError):
            await self.transport.send_poke(20002)
        with self.assertRaises(PermissionError):
            await self.transport._call_action("send_private_msg", {"user_id": 20002, "message": "raw CQ string"})
        with self.assertRaises(PermissionError):
            await self.transport._call_action("get_image", {"file": "untrusted"})

    def test_autonomy_scope_is_an_exact_subset_of_transport_scopes(self):
        self.assertTrue(self.transport.can_autonomous("onebot:10001:private:20002"))
        self.assertFalse(self.transport.can_autonomous("onebot:10001:group:30003"))
        self.assertFalse(self.transport.can_autonomous("onebot:10002:private:20002"))


if __name__ == "__main__":
    unittest.main()
