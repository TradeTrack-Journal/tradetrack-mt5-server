"""Explicit, allowlisted controller telemetry; never capture native payloads."""
import os
import re
import sqlite3
from pathlib import Path
from contextlib import closing
import threading
import time

_sdk = None
_seen = {}
_pending = {}
_lock = threading.Lock()
_state_path = None
REMINDER_SECONDS = 6 * 3600
TRANSIENT_SECONDS = 180
LOCAL_ONLY = {'SLOT_BUSY', 'SERVER_PREPARING', 'AUTH_FAILED', 'INVALID_PASSWORD',
              'INVESTOR_UNVERIFIED', 'TRADING_ENABLED', 'SERVER_NOT_FOUND',
              'SERVER_NOT_FOUND_PREPARATION'}
TRANSIENT = {'TERMINAL_CHANGED', 'CATALOGUE_CHANGED', 'TERMINAL_QUARANTINED',
             'UI_DIALOG_UNAVAILABLE', 'UI_CLEANUP_FAILED', 'TERMINAL_UI_BUSY',
             'TERMINAL_STOP_TIMEOUT', 'API_HTTP_409', 'NETWORK_UNAVAILABLE'}
RECOVERED = {'collected', 'idle', 'restarted', 'CATALOGUE_SYNC_PENDING_VERIFICATION'}


def safe_slot(value):
    return value if isinstance(value, str) and re.fullmatch(r'demo-(?:0[1-9]|1[0-6])', value) else 'controller'


def reserve_event(code, now):
    """One event per code across slots/processes; no credentials in the store."""
    if _state_path:
        try:
            with closing(sqlite3.connect(str(_state_path), timeout=0.25)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS sent (code TEXT PRIMARY KEY, at REAL NOT NULL)')
                changed = db.execute('INSERT INTO sent(code, at) VALUES (?, ?) '
                                     'ON CONFLICT(code) DO UPDATE SET at=excluded.at WHERE sent.at<=? OR sent.at>?',
                                     (code, now, now - REMINDER_SECONDS, now + 60)).rowcount
                db.execute('DELETE FROM sent WHERE at<?', (now - 7 * 86400,))
                _seen[code] = db.execute('SELECT at FROM sent WHERE code=?', (code,)).fetchone()[0]
            return bool(changed)
        except (OSError, sqlite3.Error):
            # A telemetry store failure must neither stop collection nor flood
            # Sentry: retain the in-process quota as a bounded fallback.
            pass
    previous = _seen.get(code)
    if previous is not None and 0 <= now - previous < REMINDER_SECONDS:
        return False
    if len(_seen) >= 256:
        _seen.pop(next(iter(_seen)))
    _seen[code] = now
    return True


def sanitize(event, hint):
    tags = event.get('tags', {})
    code = tags.get('error_code', '')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
        return None
    return {
        'event_id': event.get('event_id'),
        'timestamp': event.get('timestamp'),
        'environment': 'production',
        'platform': 'python',
        'level': 'info' if code == 'MONITORING_CHECK' else 'error',
        'message': 'MT5 worker: ' + code,
        'fingerprint': ['mt5-worker', code],
        'tags': {'component': 'mt5-worker', 'error_code': code,
                 'slot': safe_slot(tags.get('slot'))},
    }


def initialize(state_path=None):
    global _sdk, _state_path
    _state_path = Path(state_path) if state_path else None
    if not os.environ.get('MT5_SENTRY_DSN'):
        return
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=os.environ['MT5_SENTRY_DSN'], environment='production',
                        default_integrations=False, auto_enabling_integrations=False,
                        send_default_pii=False, include_local_variables=False,
                        before_send=sanitize, transport_queue_size=32,
                        shutdown_timeout=2)
        _sdk = sentry_sdk
    except Exception:
        print('{"errorCode":"TELEMETRY_INIT_FAILED"}', flush=True)


def report(result):
    if not isinstance(result, dict):
        return
    try:
        slot = safe_slot(result.get('slotId'))
        if not result.get('errorCode'):
            if result.get('state') in RECOVERED:
                with _lock:
                    for key in list(_pending):
                        if key[1] == slot:
                            del _pending[key]
            return
        if _sdk is None:
            return
        code = result['errorCode']
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
            code = 'WORKER_FAILED'
        if code in LOCAL_ONLY:
            return
        key = (code, slot)
        with _lock:
            now = time.monotonic()
            if code in TRANSIENT or code.startswith('DISCOVERY_UI_BUSY_'):
                first, last, count = _pending.get(key, (now, now, 0))
                if now - last > 300:
                    first, count = now, 0
                if len(_pending) >= 256 and key not in _pending:
                    _pending.pop(next(iter(_pending)))
                _pending[key] = (first, now, count + 1)
                if count + 1 < 3 or now - first < TRANSIENT_SECONDS:
                    return
            if not reserve_event(code, time.time()):
                return
        return _sdk.capture_event({'tags': {'error_code': code, 'slot': slot}})
    except Exception:
        return None


def flush():
    if _sdk:
        try:
            _sdk.flush(timeout=2)
        except Exception:
            pass
