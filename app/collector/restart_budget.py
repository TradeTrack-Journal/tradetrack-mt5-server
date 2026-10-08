"""Cross-process, durable restart throttling. Caller still needs the API fence.

Apply to graceful and forced recovery alike, not just UI watchdog recovery.
Rechecking the same fenced process is idempotent; a replacement is a new attempt.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from .windows_inventory import InventoryError


def reserve_restart(executable, identity, now=None):
    path = Path(executable).resolve()
    # All configured production slots share this directory and transaction lock.
    database = path.parent.parent / '.restart-budget.sqlite3'
    key = str(path).casefold()
    now = time.time() if now is None else now
    try:
        with closing(sqlite3.connect(str(database), timeout=1)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts '
                       '(slot TEXT PRIMARY KEY, identity TEXT NOT NULL, at REAL NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT identity, at FROM attempts WHERE slot=?', (key,)).fetchone()
            if previous and previous[0] == identity:
                return
            latest = db.execute('SELECT MAX(at) FROM attempts').fetchone()[0]
            if ((latest is not None and now - latest < 120) or
                    (previous and now - previous[1] < 900)):
                raise InventoryError('RECOVERY_COOLDOWN')
            db.execute('INSERT INTO attempts VALUES (?, ?, ?) ON CONFLICT(slot) '
                       'DO UPDATE SET identity=excluded.identity, at=excluded.at', (key, identity, now))
    except (sqlite3.Error, OSError):
        raise InventoryError('RECOVERY_BUDGET_UNAVAILABLE') from None
