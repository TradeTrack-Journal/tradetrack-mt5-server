"""Explicit, allowlisted controller telemetry; never capture native payloads."""
import os
import re
import threading
import time

_sdk = None
_seen = {}
_lock = threading.Lock()


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
                 'slot': tags.get('slot') if re.fullmatch(r'demo-0[1-5]', str(tags.get('slot', ''))) else 'controller'},
    }


def initialize():
    global _sdk
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
    if _sdk is None or not isinstance(result, dict) or not result.get('errorCode'):
        return
    try:
        code = result['errorCode']
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
            code = 'WORKER_FAILED'
        slot = result.get('slotId', 'controller')
        slot = slot if isinstance(slot, str) and re.fullmatch(r'demo-0[1-5]', slot) else 'controller'
        key = (code, slot)
        with _lock:
            now = time.monotonic()
            if key in _seen and now - _seen[key] < 900:
                return
            if len(_seen) >= 256:
                _seen.pop(next(iter(_seen)))
            _seen[key] = now
        return _sdk.capture_event({'tags': {'error_code': code, 'slot': slot}})
    except Exception:
        return None


def flush():
    if _sdk:
        try:
            _sdk.flush(timeout=2)
        except Exception:
            pass
