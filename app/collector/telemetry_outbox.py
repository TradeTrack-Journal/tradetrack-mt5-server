"""Bounded, credential-free durable diagnostics; delivery runs off collection threads."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4
from .diagnostics import api_fields, EXPLANATIONS

EXTRA_CODES = {'UI_TIMEOUT', 'MONITORING_CHECK', 'UI_COMMAND_FAILED', 'SERVER_LIST_CHANGED',
               'CATALOGUE_UNAVAILABLE', 'HEALTH_API_UNAVAILABLE', 'CATALOGUE_CHANGED',
               'TERMINAL_QUARANTINED', 'WORKER_LOG_WRITE_FAILED', 'WORKER_START_FAILED',
               'API_RESPONSE_INVALID', 'API_RETRY_LATER', 'API_HTTP_500', 'API_HTTP_502',
               'API_HTTP_503', 'API_HTTP_504', 'LEASE_EXPIRED', 'CHILD_FAILED',
               'POSITIONS_UNAVAILABLE', 'HISTORY_UNAVAILABLE', 'TERMINAL_IPC_UNAVAILABLE'}
CRITICAL = {'ACCOUNT_IDENTITY_MISMATCH', 'RESULT_CONFLICT'}


class DiagnosticOutbox:
    def __init__(self, path, client, release=None, start=True):
        self.path = Path(path)
        self.client = client
        self.release = release
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS diagnostic_outbox (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL, next_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS diagnostic_delivered (key TEXT PRIMARY KEY, at REAL NOT NULL)")
        if start:
            self.thread = threading.Thread(target=self.run, name='mt5-diagnostics', daemon=True)
            self.thread.start()

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=0.25)

    def capture_event(self, event):
        tags = event.get('tags', {})
        code = tags.get('error_code')
        if code not in EXPLANATIONS and code not in EXTRA_CODES:
            code = 'WORKER_FAILED'
        fields = api_fields(tags.get('operation'), tags.get('api_reason'))
        payload = {'eventId': uuid4().hex, 'errorCode': code, **fields}
        slot = tags.get('slot')
        if isinstance(slot, str) and slot.startswith('demo-') and slot[5:].isdigit() and 1 <= int(slot[5:]) <= 16:
            payload['slotId'] = slot
        native = tags.get('native_error_code')
        if type(native) is int and -10010 <= native <= -1:
            payload['nativeErrorCode'] = native
        if self.release:
            payload['release'] = self.release
        reason = fields.get('apiReason')
        key = code + (':' + reason if reason in CRITICAL else '')
        now = time.time()
        with closing(self.connect()) as db, db:
            # Never grow without bound during an extended outage.
            expired = db.execute("DELETE FROM diagnostic_outbox WHERE created<?", (now - 7 * 86400,)).rowcount
            if expired:
                print(json.dumps({'event': 'MT5_DIAGNOSTIC_EXPIRED', 'count': expired}), flush=True)
            db.execute("DELETE FROM diagnostic_delivered WHERE at<?", (now - 7 * 86400,))
            prior = db.execute("SELECT at FROM diagnostic_delivered WHERE key=?", (key,)).fetchone()
            if prior and 0 <= now - prior[0] < 6 * 3600:
                return None
            if db.execute("SELECT count(*) FROM diagnostic_outbox").fetchone()[0] >= 256:
                print('{"event":"MT5_DIAGNOSTIC_QUEUE_FULL"}', flush=True)
                return None
            inserted = db.execute("INSERT OR IGNORE INTO diagnostic_outbox(key,payload,created,next_at) VALUES(?,?,?,?)",
                                  (key, json.dumps(payload), now, now)).rowcount
        self.wake.set()
        return payload['eventId'] if inserted else None

    def drain_one(self):
        now = time.time()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT key,payload,attempts FROM diagnostic_outbox WHERE next_at<=? ORDER BY created LIMIT 1", (now,)).fetchone()
            if not row:
                return False
            key, encoded, attempts = row
            # Cross-process lease survives watchdog/worker races and process crashes.
            db.execute("UPDATE diagnostic_outbox SET next_at=?, attempts=attempts+1 WHERE key=?", (now + 120, key))
        payload = json.loads(encoded)
        try:
            response = self.client.call('/diagnostics', payload)
            accepted = response.get('accepted') is True
            retry = response.get('retryAfterSeconds', 300)
            retry = min(3600, max(60, retry)) if type(retry) is int else 300
            status = 'accepted' if accepted else 'retry'
            channel = response.get('channel') if response.get('channel') in ('sentry', 'telegram') else None
        except Exception:
            accepted, retry, status, channel = False, min(300, 30 * 2 ** min(attempts, 4)), 'transport_failed', None
        with closing(self.connect()) as db, db:
            if accepted:
                db.execute("DELETE FROM diagnostic_outbox WHERE key=? AND payload=?", (key, encoded))
                db.execute("INSERT INTO diagnostic_delivered(key,at) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET at=excluded.at", (key, time.time()))
            else:
                db.execute("UPDATE diagnostic_outbox SET next_at=? WHERE key=? AND payload=?", (time.time() + retry, key, encoded))
        print(json.dumps({'event': 'MT5_DIAGNOSTIC_DELIVERY', 'eventId': payload['eventId'],
                          'errorCode': payload['errorCode'], 'status': status, 'channel': channel}), flush=True)
        return True

    def run(self):
        while not self.stopped.is_set():
            try:
                if self.drain_one():
                    continue
            except Exception:
                print('{"event":"MT5_DIAGNOSTIC_STORE_FAILED"}', flush=True)
            self.wake.wait(30)
            self.wake.clear()

    def flush(self, timeout=2):
        # Pending messages remain durable for the next process.
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout)
