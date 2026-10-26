"""SQLite is the authority for events, scoped memories, budgets and delivery.

Reflections are deliberately NOT evidence. No model can select its own scope.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def uid(prefix):
    return prefix + ":" + uuid.uuid4().hex


class Store:
    def __init__(self, path, *, daily_replies=100):
        self.set_reply_limit(daily_replies)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        PRAGMA busy_timeout=5000;
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,scope TEXT NOT NULL,
          kind TEXT NOT NULL,text TEXT NOT NULL,sender TEXT NOT NULL,occurred REAL NOT NULL,
          received REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER DEFAULT 0,
          error TEXT,thread_id TEXT,epoch INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS event_queue ON events(status,occurred,seq);
        CREATE TABLE IF NOT EXISTS evidence(
          id TEXT PRIMARY KEY,scope TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
          created REAL NOT NULL,source TEXT,hash TEXT,parent_event TEXT);
        CREATE INDEX IF NOT EXISTS evidence_scope ON evidence(scope,created);
        CREATE TABLE IF NOT EXISTS memories(
          id TEXT PRIMARY KEY,scope TEXT NOT NULL,subject TEXT NOT NULL,kind TEXT NOT NULL,
          key TEXT NOT NULL,content TEXT NOT NULL,evidence_ids TEXT NOT NULL,confidence REAL NOT NULL,
          created REAL NOT NULL,updated REAL NOT NULL,expires REAL,status TEXT NOT NULL DEFAULT 'active');
        CREATE INDEX IF NOT EXISTS memory_scope ON memories(scope,status);
        CREATE TABLE IF NOT EXISTS behavior(
          id TEXT PRIMARY KEY,scope TEXT NOT NULL,subject TEXT NOT NULL,key TEXT NOT NULL,
          value TEXT NOT NULL,evidence_ids TEXT NOT NULL,created REAL NOT NULL,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS reflections(
          id TEXT PRIMARY KEY,scope TEXT NOT NULL,event_id TEXT NOT NULL,summary TEXT NOT NULL,
          diary TEXT NOT NULL,changes TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(
          id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL,
          status TEXT NOT NULL,attempts INTEGER DEFAULT 0,next_try REAL NOT NULL,expires REAL NOT NULL,
          created REAL NOT NULL,receipt TEXT,error TEXT,epoch INTEGER NOT NULL,reply_day TEXT);
        CREATE TABLE IF NOT EXISTS reply_days(
          day TEXT PRIMARY KEY,used INTEGER NOT NULL CHECK(used>=0));
        CREATE TABLE IF NOT EXISTS reservations(
          id TEXT PRIMARY KEY,day TEXT NOT NULL,scope TEXT NOT NULL,mode TEXT NOT NULL,
          tokens INTEGER NOT NULL,actual INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS control(scope TEXT PRIMARY KEY,epoch INTEGER DEFAULT 0,
          paused INTEGER DEFAULT 0,last_message REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deletions(scope TEXT PRIMARY KEY,before REAL NOT NULL);
        """)
        with self.tx() as db:
            if 'reply_day' not in {row['name'] for row in db.execute('PRAGMA table_info(outbox)')}:
                # Migrate existing deliveries once. Keep only anonymous date/count totals.
                db.execute('ALTER TABLE outbox ADD COLUMN reply_day TEXT')
                for row in db.execute('SELECT * FROM outbox').fetchall():
                    if row['status'] in {'pending','sending','sent','uncertain','failed'} or row['attempts']:
                        day=self.reply_day(row['created'])
                        self._charge_reply(db,day)
                        db.execute('UPDATE outbox SET reply_day=? WHERE id=?',(day,row['id']))

    def set_reply_limit(self, limit):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('invalid daily reply limit')
        self.daily_replies=limit

    @staticmethod
    def reply_day(now):
        # The delivery allowance always follows the owner's Shanghai civil day.
        return datetime.fromtimestamp(now,ZoneInfo('Asia/Shanghai')).date().isoformat()

    @staticmethod
    def _charge_reply(db, day):
        db.execute('INSERT INTO reply_days(day,used) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1',(day,))

    @staticmethod
    def _reply_used(db, day):
        row=db.execute('SELECT used FROM reply_days WHERE day=?',(day,)).fetchone()
        return row['used'] if row else 0

    def reply_usage(self, now=None):
        now=time.time() if now is None else now
        with self.tx() as db:
            self._expire_pending(db,now)
            return self._reply_used(db,self.reply_day(now))

    @staticmethod
    def _release_unattempted_reply(db, row):
        # An attempted or ambiguous delivery is never refunded, even on deletion.
        if row['status']=='pending' and row['attempts']==0 and row['reply_day']:
            db.execute('UPDATE reply_days SET used=used-1 WHERE day=? AND used>0',(row['reply_day'],))
            db.execute('UPDATE outbox SET reply_day=NULL WHERE id=?',(row['id'],))

    def _cancel_pending(self, db, scope):
        for row in db.execute("SELECT * FROM outbox WHERE scope=? AND status='pending'",(scope,)).fetchall():
            self._release_unattempted_reply(db,row)
        db.execute("UPDATE outbox SET status='cancelled' WHERE scope=? AND status='pending'",(scope,))

    def _expire_pending(self, db, now):
        for row in db.execute("SELECT * FROM outbox WHERE status='pending' AND expires<=?",(now,)).fetchall():
            self._release_unattempted_reply(db,row)
        db.execute("UPDATE outbox SET status='expired' WHERE status='pending' AND expires<=?",(now,))

    def _reserve_delivery_day(self, db, row, now):
        day=self.reply_day(now)
        used=self._reply_used(db,day)
        if row['reply_day']!=day:
            if used>=self.daily_replies:return False
            self._release_unattempted_reply(db,row)
            self._charge_reply(db,day)
            db.execute('UPDATE outbox SET reply_day=? WHERE id=?',(day,row['id']))
        elif used>self.daily_replies:
            # A trusted owner may lower the cap while old reservations still exist.
            return False
        return True

    def prepare_delivery(self, oid, now=None):
        """Final host-side check immediately before initiating a QQ send attempt.

        Remote arrival time is unknowable. A midnight crossed during rate-limit
        sleep must reserve the new local day before any network send starts.
        """
        now=time.time() if now is None else now
        with self.tx() as db:
            row=db.execute("SELECT o.* FROM outbox o JOIN control c USING(scope) WHERE o.id=? AND o.status='sending' AND o.expires>? AND o.epoch=c.epoch AND c.paused=0",(oid,now)).fetchone()
            return bool(row and self._reserve_delivery_day(db,row,now))

    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            if self.db.in_transaction:
                # Nested store operations share one outer atomic boundary.
                yield self.db
                return
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def close(self):
        self.db.close()

    def ingest(self, event_id, scope, kind, text, sender, occurred, now=None):
        now = time.time() if now is None else now
        if kind not in {"message", "autonomy"} or len(text) > 8000:
            raise ValueError("unsupported event or oversized input")
        with self.tx() as db:
            deletion=db.execute('SELECT before FROM deletions WHERE scope=?',(scope,)).fetchone()
            if deletion and occurred<=deletion['before']:return False
            db.execute("INSERT OR IGNORE INTO control(scope) VALUES(?)", (scope,))
            ctrl = db.execute("SELECT * FROM control WHERE scope=?", (scope,)).fetchone()
            late = kind == "message" and occurred < ctrl["last_message"]
            status = "late" if late else "pending"
            result = db.execute("INSERT OR IGNORE INTO events(id,scope,kind,text,sender,occurred,received,status,epoch) VALUES(?,?,?,?,?,?,?,?,?)",
                                (event_id, scope, kind, text, str(sender), occurred, now, status, ctrl["epoch"]))
            if not result.rowcount:
                return False
            # Only observations, never autonomous prompt text, can become evidence.
            if kind == "message":
                db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",
                           (event_id, scope, "message", dumps({"text": text, "sender": str(sender)}), occurred, "onebot", None, event_id))
                if text.strip() == '/stop' and not late:
                    db.execute('UPDATE control SET epoch=epoch+1 WHERE scope=?',(scope,))
                    db.execute("UPDATE events SET status='cancelled' WHERE scope=? AND status IN ('pending','running')",(scope,))
                    self._cancel_pending(db,scope)
            return True

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.tx() as db:
            row = db.execute("SELECT e.* FROM events e JOIN control c USING(scope) WHERE e.status='pending' AND c.paused=0 AND e.epoch=c.epoch ORDER BY e.occurred,e.seq LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE events SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
            if row["kind"] == "message":
                db.execute("UPDATE control SET last_message=MAX(last_message,?) WHERE scope=?", (row["occurred"],row["scope"]))
            return dict(row)

    def recover(self):
        # Unknown delivery cannot be retried safely: OneBot offers no idempotency key.
        with self.tx() as db:
            db.execute("UPDATE outbox SET status='uncertain',error='process restarted during send' WHERE status='sending'")
            for row in db.execute("SELECT * FROM events WHERE status='running'").fetchall():
                recorded=db.execute("SELECT payload FROM evidence WHERE parent_event=? AND kind='outcome' ORDER BY created DESC LIMIT 1",(row['id'],)).fetchone()
                completed=json.loads(recorded['payload'])['status'] if recorded else None
                status=completed if completed in ('quiet','queued','sent','uncertain','expired','cancelled') else ('pending' if row['attempts']<2 else 'failed')
                db.execute('UPDATE events SET status=? WHERE id=?',(status,row['id']))

    def set_event(self, event_id, status, error=None, thread_id=None):
        with self.tx() as db:
            db.execute("UPDATE events SET status=?,error=?,thread_id=COALESCE(?,thread_id) WHERE id=? AND status!='cancelled' AND EXISTS (SELECT 1 FROM control c WHERE c.scope=events.scope AND c.epoch=events.epoch)", (status,error,thread_id,event_id))

    def evidence(self, scope, ids):
        if not ids or len(ids)>8:
            raise ValueError("changes require 1..8 independent evidence ids")
        found = self.rows("SELECT * FROM evidence WHERE scope=? AND id IN (%s)" % ",".join("?"*len(ids)), (scope,*ids))
        if len(found) != len(set(ids)):
            raise ValueError("missing or cross-scope evidence")
        return found

    def add_source(self, scope, source, payload, parent_event, now=None, epoch=None):
        now = time.time() if now is None else now
        raw = dumps(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        evidence_id = "source:" + hashlib.sha256((scope+source+digest).encode()).hexdigest()[:32]
        with self.tx() as db:
            if epoch is not None and not db.execute('SELECT 1 FROM control WHERE scope=? AND epoch=? AND paused=0',(scope,epoch)).fetchone():raise ValueError('interrupted source result')
            db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?)", (evidence_id,scope,"source",raw,now,source,digest,parent_event))
        return evidence_id

    def record_outcome(self, event_id, scope, outcome, now=None, epoch=None):
        now = time.time() if now is None else now
        evidence_id = "outcome:"+event_id+":"+outcome["status"]
        with self.tx() as db:
            if epoch is not None and not db.execute('SELECT 1 FROM control WHERE scope=? AND epoch=? AND paused=0',(scope,epoch)).fetchone():raise ValueError('interrupted outcome')
            db.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?,?,?)", (evidence_id,scope,"outcome",dumps(outcome),now,"bridge",None,event_id))
        return evidence_id

    def remember(self, scope, sender, candidate, now=None):
        now = time.time() if now is None else now
        kind = candidate["kind"]
        if kind not in {"episodic","knowledge","user_observation","preference","lesson"}:
            raise ValueError("unsupported memory kind")
        evidence = self.evidence(scope, candidate["evidence_ids"])
        if kind=='episodic' and not any(e['kind'] in {'message','source'} for e in evidence):
            raise ValueError('episodes need actual observations')
        if kind == "knowledge" and not any(e["kind"]=="source" for e in evidence):
            raise ValueError("knowledge needs retrieved source evidence")
        if kind in {"user_observation","preference"}:
            if not any(e["kind"]=="message" and json.loads(e["payload"])["sender"]==str(sender) for e in evidence):
                raise ValueError("person observation requires this person's message")
        subject = str(sender) if kind in {"user_observation","preference"} else "*"
        confidence = max(0.05,min(float(candidate["confidence"]),0.85))
        if kind in {"user_observation","preference"} and len(evidence)<2:
            confidence = min(confidence,0.55)
        content = str(candidate["content"]).strip()[:1200]
        key = str(candidate.get("key") or content)[:120]
        if not content:
            raise ValueError("empty memory")
        memory_id = uid("memory")
        with self.tx() as db:
            duplicate=db.execute("SELECT id FROM memories WHERE scope=? AND subject=? AND kind=? AND key=? AND content=? AND evidence_ids=? AND status='active'",(scope,subject,kind,key,content,dumps(candidate['evidence_ids']))).fetchone()
            if duplicate:return duplicate['id']
            db.execute("UPDATE memories SET status='superseded' WHERE scope=? AND subject=? AND kind=? AND key=? AND status='active'", (scope,subject,kind,key))
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (memory_id,scope,subject,kind,key,content,dumps(candidate["evidence_ids"]),confidence,now,now,now+86400*(90 if kind in {"user_observation","preference"} else 365),"active"))
        return memory_id

    def adapt(self, scope, sender, candidate, now=None):
        now = time.time() if now is None else now
        key, value = candidate["key"], candidate["value"]
        evs = self.evidence(scope,candidate["evidence_ids"])
        texts = "\n".join(json.loads(e["payload"])["text"] for e in evs if e["kind"]=="message" and json.loads(e["payload"])["sender"]==str(sender))
        subject = str(sender)
        if key == "reply_max_chars":
            if not re.search(r"简短|短一点|少说|详细|展开|多说",texts):
                raise ValueError("length change requires explicit feedback")
            if not isinstance(value,(int,float)) or isinstance(value,bool):
                raise ValueError("invalid length")
            value = max(40,min(int(value),1200))
        elif key == "ask_followups":
            if not isinstance(value,bool) or not re.search(r"反问|追问|问我|问句|问题",texts):
                raise ValueError("question style requires explicit feedback")
        elif key == "interest":
            if not isinstance(value,str) or not value.strip() or len(value)>80 or not any(e["kind"]=="source" for e in evs):
                raise ValueError("interest needs real exploration")
            # One small addition per 24 hours; identity is never replaced.
            if self.rows("SELECT id FROM behavior WHERE scope=? AND key='interest' AND created>?",(scope,now-86400)):
                raise ValueError("interest change cooling down")
            subject="*"
        else:
            raise ValueError("operating boundaries are not adaptive settings")
        version = uid("behavior")
        with self.tx() as db:
            duplicate=db.execute('SELECT id FROM behavior WHERE scope=? AND subject=? AND key=? AND value=? AND evidence_ids=? AND active=1',(scope,subject,key,dumps(value),dumps(candidate['evidence_ids']))).fetchone()
            if duplicate:return duplicate['id']
            db.execute("INSERT INTO behavior VALUES(?,?,?,?,?,?,?,1)",(version,scope,subject,key,dumps(value),dumps(candidate["evidence_ids"]),now))
        return version

    def settings(self, scope, sender):
        values = {"reply_max_chars":320,"ask_followups":False,"interests":[]}
        for row in self.rows("SELECT * FROM behavior WHERE scope=? AND subject IN (?, '*') AND active=1 ORDER BY created,rowid", (scope,str(sender))):
            value=json.loads(row["value"])
            if row["key"]=="interest":
                if value not in values["interests"]: values["interests"].append(value)
            else: values[row["key"]]=value
        values["interests"]=values["interests"][-5:]
        return values

    def rollback_behavior(self, version):
        with self.tx() as db:
            return db.execute("UPDATE behavior SET active=0 WHERE id=?",(version,)).rowcount

    def retrieve(self, scope, sender, query, limit=8, now=None):
        now=time.time() if now is None else now
        candidates=self.rows("SELECT * FROM memories WHERE scope=? AND subject IN (?, '*') AND status='active' AND (expires IS NULL OR expires>?) ORDER BY updated DESC LIMIT 200",(scope,str(sender),now))
        # Chinese bigrams + Latin tokens, deterministic bounded retrieval, no whole archive.
        terms=set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2}", query.lower()))
        terms.update(query[i:i+2] for i in range(len(query)-1) if all('\u4e00'<=c<='\u9fff' for c in query[i:i+2]))
        def score(row):
            return (sum(t in row["content"].lower() for t in terms),row["kind"]=="lesson",row["updated"])
        candidates.sort(key=score,reverse=True)
        return candidates[:min(limit,12)]

    def recent(self, scope, limit=6):
        return list(reversed(self.rows("SELECT id,text,sender,occurred,status FROM events WHERE scope=? AND kind='message' ORDER BY seq DESC LIMIT ?",(scope,limit))))

    def reserve(self, scope, mode, day, tokens, daily_tokens, daily_calls, now=None):
        with self.tx() as db:
            row=db.execute("SELECT COALESCE(SUM(tokens),0) AS n,COUNT(*) AS c FROM reservations WHERE day=?",(day,)).fetchone()
            if row["n"]+tokens>daily_tokens or row["c"]>=daily_calls:
                return None
            rid=uid("run")
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,0,?)",(rid,day,scope,mode,tokens,time.time() if now is None else now))
            return rid

    def account(self, rid, actual):
        with self.tx() as db:
            db.execute("UPDATE reservations SET actual=?,tokens=MAX(tokens,?) WHERE id=?",(actual,actual,rid))

    def reflection(self, scope, event_id, summary, diary, changes, now=None,epoch=None):
        with self.tx() as db:
            if epoch is not None and not db.execute('SELECT 1 FROM control WHERE scope=? AND epoch=? AND paused=0',(scope,epoch)).fetchone():return
            db.execute("INSERT INTO reflections VALUES(?,?,?,?,?,?,?)",(uid("reflection"),scope,event_id,summary[:1500],diary[:2000],dumps(changes),time.time() if now is None else now))

    def enqueue(self, event, body, ttl, now=None):
        now=time.time() if now is None else now
        with self.tx() as db:
            self._expire_pending(db,now)
            ctrl=db.execute("SELECT * FROM control WHERE scope=?",(event["scope"],)).fetchone()
            if not ctrl or ctrl["epoch"]!=event["epoch"] or ctrl["paused"]:
                return False
            day=self.reply_day(now)
            if ttl<=0 or self._reply_used(db,day)>=self.daily_replies:return False
            inserted=db.execute("INSERT OR IGNORE INTO outbox(id,event_id,scope,body,status,next_try,expires,created,epoch,reply_day) VALUES(?,?,?,?, 'pending',?,?,?,?,?)",(uid("send"),event["id"],event["scope"],body,now,now+ttl,now,event["epoch"],day)).rowcount
            if inserted:self._charge_reply(db,day)
            return bool(inserted)

    def next_delivery(self, now=None):
        now=time.time() if now is None else now
        with self.tx() as db:
            self._expire_pending(db,now)
            candidates=db.execute("SELECT o.* FROM outbox o JOIN control c USING(scope) WHERE o.status='pending' AND o.next_try<=? AND o.expires>? AND o.epoch=c.epoch AND c.paused=0 ORDER BY o.created",(now,now)).fetchall()
            for row in candidates:
                # A blocked old-day reservation must not stall today's reserved replies.
                if not self._reserve_delivery_day(db,row,now):continue
                db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?",(row["id"],))
                return dict(row)
            return None

    def delivery_result(self, oid, status, *, receipt=None,error=None,now=None):
        now=time.time() if now is None else now
        with self.tx() as db:
            row=db.execute("SELECT * FROM outbox WHERE id=?",(oid,)).fetchone()
            if not row:return
            if status in {'cancelled','expired'}:self._release_unattempted_reply(db,row)
            if status=='retry' and row['attempts']>=5:status='failed'
            retry_at=now+min(300,2**row['attempts'])
            db.execute("UPDATE outbox SET status=?,receipt=?,error=?,next_try=? WHERE id=?",('pending' if status=='retry' else status,dumps(receipt) if receipt else None,error,retry_at,oid))
        if status!='retry':
            self.record_outcome(row['event_id'],row['scope'],{"status":status,"receipt":receipt},now)

    def pause(self,scope,paused=True):
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO control(scope) VALUES(?)",(scope,))
            db.execute("UPDATE control SET paused=?,epoch=epoch+1 WHERE scope=?",(int(paused),scope))
            db.execute("UPDATE events SET status='cancelled' WHERE scope=? AND status IN ('pending','running')",(scope,))
            self._cancel_pending(db,scope)

    def forget(self,scope,memory_id=None,now=None):
        with self.tx() as db:
            if memory_id:
                # Remove derived versions so forgotten material cannot reappear via retrieval.
                return db.execute("DELETE FROM memories WHERE id=? AND scope=?",(memory_id,scope)).rowcount
            self._cancel_pending(db,scope)
            for table in ('memories','behavior','reflections','outbox','evidence','events','control'):
                db.execute(f"DELETE FROM {table} WHERE scope=?",(scope,))
            db.execute('INSERT OR REPLACE INTO deletions VALUES(?,?)',(scope,time.time() if now is None else now))
        # Keep accounting reservations (no content) to prevent deletion resetting budgets.
        with self.lock:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.db.execute('VACUUM')

    def consolidate(self,scope,now=None):
        now=time.time() if now is None else now
        with self.tx() as db:
            expired=db.execute("DELETE FROM memories WHERE scope=? AND expires<=?",(scope,now)).rowcount
            old=db.execute("DELETE FROM memories WHERE scope=? AND status='superseded' AND updated<?",(scope,now-30*86400)).rowcount
        return {"expired":expired,"old_versions":old}

    def export(self,scope):
        return {t:self.rows(f"SELECT * FROM {t} WHERE scope=?",(scope,)) for t in ('events','evidence','memories','behavior','reflections','outbox')}

    def stats(self):
        return {t:self.rows(f"SELECT COUNT(*) AS count FROM {t}")[0]["count"] for t in ('events','memories','behavior','reflections','outbox')}
