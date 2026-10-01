from threading import Event, get_ident
import unittest

from app.collector.node_agent import AgentError
from app.collector.scheduler import run_slots


class Worker:
    def __init__(self, name, action):
        self.slot = {'id': name}
        self.inspected_at = 1
        self.action = action
        self.busy = False
        self.ui_thread = get_ident()
        self.connects = 0

    def connect(self):
        assert not self.busy
        self.connects += 1

    def prepare_inventory(self):
        assert not self.busy
        assert get_ident() == self.ui_thread
        self.inspected_at = 1

    def once(self, allow_ui):
        assert not allow_ui
        assert not self.busy
        self.busy = True
        try:
            self.action()
            return {'slotId': self.slot['id'], 'state': 'collected'}
        finally:
            self.busy = False


class SchedulerTests(unittest.TestCase):
    def test_catalog_refresh_does_not_wait_for_failure_backoff_or_reconnect(self):
        output, called_at = [], []
        now = [0.0]
        worker = Worker('catalog', lambda: None)
        def once(allow_ui):
            called_at.append(now[0])
            return {'slotId': 'catalog', 'state': 'maintenance_required' if len(called_at) == 1 else 'collected'}
        worker.once = once
        def tick(seconds):
            import time
            now[0] += .1
            time.sleep(.001)
        run_slots([worker], output.append, stop=lambda: any(r['state'] == 'collected' for r in output),
                  clock=lambda: now[0], sleep=tick)
        self.assertLess(called_at[1] - called_at[0], 3)
        self.assertEqual(worker.connects, 1)

    def test_fast_slot_collects_twice_while_other_child_is_blocked(self):
        blocked = Event()
        released = Event()
        finished = Event()
        fast_calls = []
        def slow():
            blocked.set()
            self.assertTrue(released.wait(3), 'batch barrier starved the free slot')
        def fast():
            self.assertTrue(blocked.wait(1))
            fast_calls.append(1)
            if len(fast_calls) == 2:
                released.set()
                finished.set()
        workers = [Worker('slow', slow), Worker('fast', fast)]
        class Preparation:
            def once(inner, idle):
                self.assertTrue(all(not worker.busy for worker in idle))
        run_slots(workers, lambda result: None, Preparation(), stop=finished.is_set,
                  interval=0, poll_seconds=0.005)
        self.assertEqual(len(fast_calls), 2)

    def test_exception_does_not_stop_other_slot_or_leak_native_text(self):
        output = []
        def broken():
            raise RuntimeError('secret native text')
        workers = [Worker('broken', broken), Worker('healthy', lambda: None)]
        self.assertFalse(run_slots(workers, output.append, once=True, poll_seconds=0.005))
        self.assertTrue(any(row.get('state') == 'collected' for row in output))
        self.assertTrue(any(row.get('errorCode') == 'WORKER_FAILED' for row in output))
        self.assertNotIn('secret native text', str(output))

    def test_slot_recovers_after_api_outage_and_refreshes_config(self):
        output = []
        calls = []
        now = [0]
        def collect():
            calls.append(1)
            if len(calls) == 1:
                raise AgentError('API_UNAVAILABLE')
        worker = Worker('recovering', collect)
        def tick(seconds):
            # Yield to executor while advancing retry clock without a real 10s delay.
            import time
            now[0] += 1
            time.sleep(0.001)
        run_slots([worker], output.append, stop=lambda: any(r.get('state') == 'collected' for r in output),
                  clock=lambda: now[0], sleep=tick)
        self.assertEqual(len(calls), 2)
        self.assertEqual(worker.connects, 2)

    def test_drain_does_not_connect_or_claim(self):
        worker = Worker('idle', lambda: self.fail('must not collect'))
        self.assertTrue(run_slots([worker], lambda result: None, stop=lambda: True))
        self.assertEqual(worker.connects, 0)

    def test_failed_inventory_is_not_submitted(self):
        worker = Worker('offline', lambda: self.fail('unsafe collection'))
        worker.prepare_inventory = lambda: {'state': 'offline', 'errorCode': 'INVESTOR_UNVERIFIED'}
        self.assertFalse(run_slots([worker], lambda result: None, once=True, poll_seconds=0.001))
