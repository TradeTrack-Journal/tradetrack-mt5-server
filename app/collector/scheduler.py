"""Dispatch collection independently of the single serialized UI maintenance lane."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re
import time

from .node_agent import AgentError
from .windows_inventory import InventoryError
from .diagnostics import error_fields


def error_code(error):
    code = str(error) if isinstance(error, (AgentError, InventoryError)) else 'WORKER_FAILED'
    return code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code) else 'WORKER_FAILED'


@dataclass
class SlotState:
    worker: object
    future: object = None
    connected: bool = False
    due: float = 0
    started: float = 0
    failures: int = 0
    finished: bool = False
    inspecting: bool = False


def run_slots(workers, emit, preparation=None, once=False, stop=lambda: False,
              poll_seconds=0.25, interval=10, clock=time.monotonic, sleep=time.sleep):
    """At most one future per slot. Never inspect/prepare a slot with a live future.

    A drain stops new dispatch, then waits for bounded children/HTTP calls. No
    process is killed here: durable API leases and quarantine remain authoritative.
    """
    states = [SlotState(worker) for worker in workers]
    preparation_at = 0
    preparation_future = None
    reserved = set()
    failed = False

    def defer(state, error):
        nonlocal failed
        code = error_code(error)
        state.failures += 1
        state.due = clock() + min(300, 10 * 2 ** min(state.failures - 1, 5))
        state.worker.inspected_at = 0
        # Retain sessions/restart intent. Refresh authoritative config on next attempt.
        state.connected = False
        state.finished = once
        failed = True
        emit({'slotId': state.worker.slot['id'], 'state': 'retrying', 'errorCode': code, **error_fields(error)})

    def inspect(state):
        if stop():
            return None
        if not state.connected:
            state.worker.connect()
        return state.worker.prepare_inventory()

    with ThreadPoolExecutor(max_workers=len(states)) as pool, ThreadPoolExecutor(max_workers=1) as ui:
        while True:
            if preparation_future is not None and preparation_future.done():
                try:
                    result = preparation_future.result()
                    if result:
                        emit(result)
                except Exception as error:
                    emit({'state': 'SERVER_PREPARATION_RETRY', 'errorCode': error_code(error), **error_fields(error)})
                preparation_future = None
                reserved.clear()
            for state in states:
                if state.future is None or not state.future.done():
                    continue
                try:
                    result = state.future.result()
                    if state.inspecting:
                        state.inspecting = False
                        state.connected = True
                        state.future = None
                        if result:
                            emit(result)
                            state.due = clock() + max(interval, min(120, result.get('retryAfterSeconds', 0)))
                            state.finished = once
                            failed = True
                        elif not stop():
                            state.started = clock()
                            state.future = pool.submit(state.worker.once, allow_ui=False)
                        continue
                    emit(dict(result, durationMs=round((clock() - state.started) * 1000)))
                    state.failures = 0
                    # The API owns per-account cadence. A successful slot can serve
                    # the next queued account immediately; idle/error slots still
                    # back off so an empty queue cannot create a claim-request loop.
                    outcome = result.get('state')
                    delay = 0 if outcome == 'collected' else min(1, interval) if outcome == 'maintenance_required' else interval
                    state.due = clock() + delay
                    state.finished = once
                except Exception as error:
                    defer(state, error)
                state.future = None
                state.inspecting = False

            draining = stop()
            if draining or (once and all(state.finished for state in states)):
                if preparation_future is None and not any(state.future is not None for state in states):
                    return not failed
                sleep(poll_seconds)
                continue

            idle = [s for s in states if s.future is None and not s.finished and s.due <= clock()]
            # Claim cooldown is not a running job. Include all free, inspected
            # slots so staggered claim timers cannot starve catalogue propagation.
            inspected = [s.worker for s in states if s.future is None and not s.finished
                         and s.connected and s.worker.inspected_at]
            if (preparation and inspected and preparation_future is None and
                    not any(s.inspecting for s in states) and clock() >= preparation_at):
                preparation_at = clock() + 30
                reserved = {id(worker) for worker in inspected}
                preparation_future = ui.submit(lambda slots=inspected: None if stop() else preparation.once(slots))

            for state in idle:
                if stop():
                    break
                if id(state.worker) in reserved:
                    continue
                try:
                    if not state.connected or state.worker.needs_inventory():
                        state.inspecting = True
                        state.future = ui.submit(inspect, state)
                        continue
                    state.started = clock()
                    state.future = pool.submit(state.worker.once, allow_ui=False)
                except Exception as error:
                    defer(state, error)
            sleep(poll_seconds)
