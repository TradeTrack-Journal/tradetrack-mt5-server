from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.collector.ui_recovery import UiRecoveryBudget, fence_ui_failure


class UiRecoveryTests(TestCase):
    def snapshot(self, now, **extra):
        return dict({'id': 'one', 'status': 'ERROR', 'inventoryErrorCode': 'TERMINAL_UI_BUSY',
                     'processIdentity': '42:1', 'generation': 'g1',
                     'lastHeartbeat': datetime.fromtimestamp(now, timezone.utc).isoformat()}, **extra)

    def test_only_sustained_fresh_same_process_errors_are_candidates(self):
        budget = UiRecoveryBudget('unused')
        for now, expected in [(1000, False), (1060, False), (1120, True)]:
            self.assertEqual(budget.observe(self.snapshot(now), now), expected)
        self.assertFalse(budget.observe(self.snapshot(1120, processIdentity='42:2'), 1121))
        self.assertFalse(budget.observe(self.snapshot(1000), 1300))
        self.assertFalse(budget.observe(self.snapshot(1300, status='STARTING'), 1300))
        self.assertFalse(budget.observe(self.snapshot(1400, inventoryErrorCode='AUTH_FAILED'), 1400))
        self.assertFalse(budget.observe(self.snapshot(1400, lastHeartbeat=None), 1400))
        self.assertFalse(budget.observe(self.snapshot(1400, lastHeartbeat='not-a-date'), 1400))

    def test_durable_per_slot_and_pool_limits(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'budget.json'
            budget = UiRecoveryBudget(path)
            self.assertTrue(budget.reserve('one', 10000))
            restarted = UiRecoveryBudget(path)
            self.assertFalse(restarted.reserve('two', 10001))
            self.assertTrue(restarted.reserve('two', 10120))
            self.assertFalse(restarted.reserve('one', 10300))
            self.assertTrue(restarted.reserve('one', 10900))
            path.write_text('broken')
            with self.assertRaises(ValueError):
                restarted.reserve('three', 12000)

    def test_rechecks_identity_and_server_generation_under_slot_mutex(self):
        slot = {'id': 'one', 'executablePath': 'terminal.exe'}
        observed = self.snapshot(1000)
        for changed in ('none', 'generation', 'process', 'healthy'):
            agent = MagicMock()
            current = dict(observed)
            if changed == 'generation':
                current['generation'] = 'g2'
            if changed == 'healthy':
                current['status'] = 'STARTING'
            agent.remote_slots = {'one': current}
            with patch('app.collector.ui_recovery.WindowsTerminal') as native:
                native.return_value.inventory_lock.return_value = nullcontext()
                native.return_value.find_process.return_value = (42, '42:2' if changed == 'process' else '42:1')
                self.assertEqual(fence_ui_failure(agent, slot, observed), changed == 'none')
                self.assertEqual(agent.client.call.call_count, int(changed == 'none'))

    def test_active_job_rejection_never_bypassed(self):
        agent = MagicMock()
        agent.remote_slots = {'one': self.snapshot(1000)}
        agent.client.call.side_effect = RuntimeError('SLOT_BUSY')
        with patch('app.collector.ui_recovery.WindowsTerminal') as native:
            native.return_value.inventory_lock.return_value = nullcontext()
            native.return_value.find_process.return_value = (42, '42:1')
            with self.assertRaisesRegex(RuntimeError, 'SLOT_BUSY'):
                fence_ui_failure(agent, {'id': 'one', 'executablePath': 'terminal.exe'}, self.snapshot(1000))
