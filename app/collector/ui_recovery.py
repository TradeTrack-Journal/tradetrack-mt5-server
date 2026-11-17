"""Bounded recovery of persistently unhealthy UI, using the existing server fence."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import time

from .windows_inventory import WindowsTerminal

UI_ERRORS = frozenset({'TERMINAL_UI_BUSY', 'UI_DIALOG_UNAVAILABLE',
                       'UI_CLEANUP_FAILED', 'UI_TIMEOUT'})


def response_time(date_header, received_monotonic, now_monotonic):
    """Use the authenticated API clock for its heartbeats, never for leases."""
    try:
        stamp = parsedate_to_datetime(date_header)
        elapsed = now_monotonic - received_monotonic
        if stamp.tzinfo is not None and 0 <= elapsed <= 120:
            return stamp.timestamp() + elapsed
    except (ValueError, TypeError, OverflowError):
        pass
    return None


class UiRecoveryBudget:
    def __init__(self, path):
        self.path = Path(path)
        self.observations = {}

    def observe(self, slot, now, *, heartbeat_now=None):
        # now is monotonic in the watchdog; heartbeat_now is the API's UTC.
        # Never compare API timestamps against a drifting VPS wall clock.
        heartbeat_now = now if heartbeat_now is None else heartbeat_now
        key = slot['id']
        try:
            heartbeat = datetime.fromisoformat(slot['lastHeartbeat'].replace('Z', '+00:00')).timestamp()
            valid = (slot.get('status') == 'ERROR' and slot.get('inventoryErrorCode') in UI_ERRORS
                     and bool(slot.get('processIdentity')) and bool(slot.get('generation'))
                     and not slot.get('quarantineIdentity') and -1 <= heartbeat_now - heartbeat <= 120)
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            self.observations.pop(key, None)
            return False
        signature = (slot['processIdentity'], slot['generation'])
        previous = self.observations.get(key)
        if previous is None or previous[0] != signature or now - previous[2] > 120:
            self.observations[key] = (signature, now, now, 1)
            return False
        self.observations[key] = (signature, previous[1], now, previous[3] + 1)
        return now - previous[1] >= 120 and previous[3] + 1 >= 3

    def reserve(self, slot_id, now):
        # Durable cooldowns survive watchdog restarts. An unreadable file fails closed.
        state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'global': 0, 'slots': {}}
        if now - state['global'] < 120 or now - state['slots'].get(slot_id, 0) < 900:
            return False
        state['global'] = now
        state['slots'][slot_id] = now
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        return True


def fence_ui_failure(agent, slot, observed):
    native = WindowsTerminal()
    with native.inventory_lock(slot['executablePath']):
        agent.connect()
        current = agent.remote_slots[slot['id']]
        if (current.get('quarantineIdentity') or current.get('status') != 'ERROR'
                or current.get('inventoryErrorCode') not in UI_ERRORS
                or current.get('generation') != observed.get('generation')
                or current.get('processIdentity') != observed.get('processIdentity')
                or not current.get('processIdentity')):
            return False
        pid, identity = native.find_process(slot['executablePath'])
        if not pid or identity != current['processIdentity']:
            return False
        # API atomically refuses an active job and fences subsequent claims.
        agent.client.call(f"/slots/{slot['id']}/prepare", {'generation': current['generation']})
        return True
