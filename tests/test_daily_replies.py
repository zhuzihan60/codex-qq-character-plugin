"""Host/database allowance tests only: no Codex call or real QQ message is sent."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from zoneinfo import ZoneInfo

from qq_character.config import load
from qq_character.engine import Engine, MockRuntime
from qq_character.store import Store


PRIVATE = 'onebot:900:private:100'
GROUP = 'onebot:900:group:200'


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


class DailyRepliesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.store = Store(self.path)
        self.now = at('2026-10-26T12:00:00')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, eid, scope=PRIVATE, now=None, store=None):
        store = store or self.store
        now = self.now if now is None else now
        self.assertTrue(store.ingest(eid, scope, 'message', '本地测试', '100', now, now))
        return store.rows('SELECT * FROM events WHERE id=?', (eid,))[0]

    def queue(self, eid, scope=PRIVATE, now=None, ttl=3600):
        now = self.now if now is None else now
        event = self.event(eid, scope, now)
        return self.store.enqueue(event, '测试回复', ttl, now)

    def test_pending_reserves_global_hundred_and_101st_is_rejected(self):
        for index in range(100):
            self.assertTrue(self.queue(str(index), PRIVATE if index % 2 else GROUP))
        self.assertFalse(self.queue('101', GROUP))
        self.assertEqual(self.store.reply_usage(self.now), 100)
        self.assertEqual(self.store.stats()['outbox'], 100)

    def test_duplicate_does_not_consume_a_second_slot(self):
        event = self.event('same')
        self.assertTrue(self.store.enqueue(event, '一次', 300, self.now))
        self.assertFalse(self.store.enqueue(event, '重复', 300, self.now))
        self.assertEqual(self.store.reply_usage(self.now), 1)

    def test_atomic_last_slot_across_two_database_connections(self):
        self.store.set_reply_limit(1)
        other = Store(self.path, daily_replies=1)
        try:
            first = self.event('a')
            second = self.event('b', GROUP, store=other)
            barrier = threading.Barrier(2)

            def submit(store, event):
                barrier.wait(timeout=5)
                return store.enqueue(event, '争抢最后一个名额', 300, self.now)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit, self.store, first), pool.submit(submit, other, second)]
                self.assertEqual(sorted(f.result(timeout=10) for f in futures), [False, True])
            self.assertEqual(self.store.reply_usage(self.now), 1)
        finally:
            other.close()

    def test_sent_failed_sending_and_uncertain_survive_restart_and_scope_deletion(self):
        for index, status in enumerate(('sent', 'failed', 'uncertain', 'sending')):
            self.assertTrue(self.queue(str(index)))
            item = self.store.next_delivery(self.now)
            self.assertTrue(self.store.prepare_delivery(item['id'], self.now))
            if status != 'sending':
                self.store.delivery_result(item['id'], status, now=self.now)
        self.assertEqual(self.store.reply_usage(self.now), 4)
        self.store.close()
        self.store = Store(self.path, daily_replies=4)
        self.store.recover()
        self.assertEqual(self.store.reply_usage(self.now), 4)
        self.store.forget(PRIVATE, now=self.now)
        self.assertTrue(all(not values for values in self.store.export(PRIVATE).values()))
        self.assertEqual(self.store.reply_usage(self.now), 4)
        self.assertFalse(self.queue('after-delete', GROUP, self.now + 1))
        self.assertEqual(set(self.store.rows('SELECT * FROM reply_days')[0]), {'day', 'used'})

    def test_unattempted_cancellation_and_forgetting_release_once(self):
        self.queue('cancel')
        self.store.pause(PRIVATE)
        self.store.pause(PRIVATE)
        self.assertEqual(self.store.reply_usage(self.now), 0)
        self.queue('delete', GROUP)
        self.store.forget(GROUP, now=self.now)
        self.store.forget(GROUP, now=self.now)
        self.assertEqual(self.store.reply_usage(self.now), 0)

    def test_stop_releases_only_unattempted_pending(self):
        self.queue('pending')
        self.event('stop', now=self.now + 1)
        # The actual control message is ingested through the same persistent path.
        self.store.ingest('actual-stop', PRIVATE, 'message', '/stop', '100', self.now + 2, self.now + 2)
        self.assertEqual(self.store.reply_usage(self.now + 2), 0)

    def test_expiration_releases_without_an_active_transport(self):
        self.store.set_reply_limit(1)
        self.queue('expired', ttl=1)
        self.assertTrue(self.queue('new', GROUP, self.now + 2))
        self.assertEqual(self.store.reply_usage(self.now + 2), 1)
        self.assertEqual(self.store.rows('SELECT status FROM outbox WHERE event_id=?', ('expired',))[0]['status'], 'expired')

    def test_attempted_retry_cancel_and_failure_are_never_refunded(self):
        self.queue('retry')
        item = self.store.next_delivery(self.now)
        self.store.delivery_result(item['id'], 'retry', now=self.now)
        self.store.pause(PRIVATE)
        self.assertEqual(self.store.reply_usage(self.now), 1)
        self.store.forget(PRIVATE, now=self.now)
        self.assertEqual(self.store.reply_usage(self.now), 1)

    def test_calendar_day_changes_at_shanghai_midnight(self):
        self.store.set_reply_limit(1)
        before = at('2026-10-26T23:59:59')
        after = before + 2
        self.queue('old-day', now=before)
        item = self.store.next_delivery(before)
        self.store.delivery_result(item['id'], 'sent', now=before)
        self.assertFalse(self.queue('same-day', GROUP, before))
        self.assertTrue(self.queue('new-day', GROUP, after))
        self.assertEqual(self.store.reply_day(before), '2026-10-26')
        self.assertEqual(self.store.reply_day(after), '2026-10-27')

    def test_unattempted_carryover_moves_its_reservation_to_send_day(self):
        before = at('2026-10-26T23:59:59')
        after = before + 2
        self.queue('carried', now=before)
        item = self.store.next_delivery(after)
        self.assertIsNotNone(item)
        self.assertTrue(self.store.prepare_delivery(item['id'], after))
        self.assertEqual(self.store.reply_usage(before), 0)
        self.assertEqual(self.store.reply_usage(after), 1)

    def test_sending_crossing_midnight_requires_new_slot_and_keeps_old_charge(self):
        self.store.set_reply_limit(1)
        before = at('2026-10-26T23:59:59')
        after = before + 2
        self.queue('in-flight', now=before)
        item = self.store.next_delivery(before)
        self.assertTrue(self.store.prepare_delivery(item['id'], after))
        self.assertTrue(self.store.prepare_delivery(item['id'], after))
        self.assertEqual(self.store.reply_usage(before), 1)
        self.assertEqual(self.store.reply_usage(after), 1)
        self.assertFalse(self.queue('extra', GROUP, after))

    def test_midnight_final_check_rejects_when_new_day_is_full(self):
        self.store.set_reply_limit(1)
        before = at('2026-10-26T23:59:59')
        after = before + 2
        self.queue('waiting-for-rate-limit', now=before)
        item = self.store.next_delivery(before)
        self.assertTrue(self.queue('new-day-reservation', GROUP, after))
        self.assertFalse(self.store.prepare_delivery(item['id'], after))
        self.assertEqual(self.store.reply_usage(after), 1)

    def test_pending_carryover_cannot_bypass_full_new_day(self):
        self.store.set_reply_limit(1)
        before = at('2026-10-26T23:59:59')
        after = before + 2
        self.queue('old-pending', now=before)
        self.queue('new-pending', GROUP, after)
        self.assertEqual(self.store.next_delivery(after)['event_id'], 'new-pending')
        self.assertIsNone(self.store.next_delivery(after))
        self.assertEqual(self.store.reply_usage(after), 1)

    def test_legacy_outbox_migration_is_idempotent(self):
        self.store.close()
        legacy_path = Path(self.temp.name) / 'legacy.db'
        with sqlite3.connect(legacy_path) as db:
            db.execute('''CREATE TABLE outbox(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,
                scope TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER DEFAULT 0,
                next_try REAL NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,receipt TEXT,
                error TEXT,epoch INTEGER NOT NULL)''')
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('send:legacy', 'legacy', PRIVATE, '旧消息', 'sent', 1, self.now, self.now + 300, self.now, None, None, 0))
        db.close()
        self.store = Store(legacy_path)
        self.assertEqual(self.store.reply_usage(self.now), 1)
        self.store.close()
        self.store = Store(legacy_path)
        self.assertEqual(self.store.reply_usage(self.now), 1)

    def test_engine_limit_does_not_invoke_model_or_enqueue_filler(self):
        cfg = json.loads((Path(__file__).parents[1] / 'config/example.json').read_text(encoding='utf-8'))
        cfg['budgets']['daily_replies'] = 1
        cfg['timezone'] = 'UTC'  # Delivery dates still use Shanghai, independent of scheduling.
        engine = Engine(cfg, self.store, MockRuntime(), clock=lambda: self.now)
        self.queue('reserved', GROUP)
        self.store.set_event('reserved', 'queued')
        self.event('over-limit')
        self.assertTrue(engine.step())
        self.assertFalse(self.store.rows('SELECT * FROM reservations'))
        self.assertEqual(self.store.stats()['outbox'], 1)
        outcome = self.store.rows("SELECT payload FROM evidence WHERE parent_event='over-limit' AND kind='outcome'")[0]
        self.assertEqual(json.loads(outcome['payload'])['reason'], 'daily_reply_limit')

    def test_config_accepts_requested_capacity_but_keeps_finite_bounds(self):
        cfg = json.loads((Path(__file__).parents[1] / 'config/example.json').read_text(encoding='utf-8'))
        cfg['budgets'].update(turn_tokens=24000, daily_tokens=5280000, daily_model_calls=220,
                              turn_seconds=75, tools_per_turn=2)
        cfg['budgets'].pop('daily_replies', None)
        target = Path(self.temp.name) / 'config.json'
        target.write_text(json.dumps(cfg), encoding='utf-8')
        self.assertEqual(load(target)['budgets']['daily_replies'], 100)
        for key, value in [('daily_replies', 101), ('daily_replies', True), ('turn_tokens', 24001),
                           ('daily_tokens', 5280001), ('daily_model_calls', 221)]:
            invalid = json.loads(json.dumps(cfg))
            invalid['budgets'][key] = value
            target.write_text(json.dumps(invalid), encoding='utf-8')
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                load(target)


if __name__ == '__main__':
    unittest.main()
