"""Observe tick-clock offsets; never extrapolate these samples over old deals."""
import json
import os
from pathlib import Path
import time


def sample(native, symbols):
    values = {}
    candidates = sorted(set(symbols))[:6]
    if len(candidates) < 6:
        try:
            for item in native.symbols_get() or ():
                if getattr(item, 'visible', False) and item.name not in candidates:
                    candidates.append(item.name)
                    if len(candidates) == 6:
                        break
        except Exception:
            pass
    for symbol in candidates:
        try:
            tick = native.symbol_info_tick(symbol)
            value = getattr(tick, 'time_msc', None)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                values[symbol] = value
        except Exception:
            continue
    return {'utcMs': time.time_ns() // 1_000_000, 'monotonicMs': time.monotonic_ns() // 1_000_000, 'ticks': values}


def compare(first, second):
    elapsed = second['monotonicMs'] - first['monotonicMs']
    wall_elapsed = second['utcMs'] - first['utcMs']
    if not 500 <= elapsed <= 10000 or abs(wall_elapsed - elapsed) > 250:
        return {'status': 'CLOCK_UNSTABLE'}
    offsets = []
    for symbol, previous in first['ticks'].items():
        current = second['ticks'].get(symbol)
        if current is None or current <= previous:
            continue
        offset = round((current - second['utcMs']) / 900000) * 15
        if not -840 <= offset <= 840:
            continue
        lag = second['utcMs'] - (current - offset * 60000)
        previous_lag = first['utcMs'] - (previous - offset * 60000)
        if -250 <= lag <= 2000 and -250 <= previous_lag <= 2000:
            offsets.append(offset)
    if len(offsets) < 2:
        return {'status': 'FRESH_QUOTES_REQUIRED'}
    if len(set(offsets)) != 1:
        return {'status': 'CLOCK_DISAGREEMENT'}
    return {'status': 'OBSERVED', 'offsetMinutes': offsets[0], 'symbols': len(offsets),
            'fromUtcMs': first['utcMs'], 'toUtcMs': second['utcMs'], 'basis': 'advancing_tick_clock'}


def record(server, first, second):
    """Optional local evidence sink; contains no account identifiers or passwords.

    Tick timestamps do not establish a broker's historical deal timezone. Keep
    evidence separate from reviewed conversion policies. Daily files are stored
    independently of terminal profiles and active servers.dat files.
    """
    destination = os.environ.get('MT5_CLOCK_OBSERVATIONS_DIR')
    if not destination:
        return
    try:
        result = compare(first, second)
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (time.strftime('%Y-%m-%d', time.gmtime()) + '.jsonl')
        # Independent native children may append; a single small OS append is used.
        body = (json.dumps({'server': server, 'observedAtMs': second['utcMs'], **result}, separators=(',', ':')) + '\n').encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, OverflowError):
        # A diagnostic storage failure must never interrupt trade collection.
        return
