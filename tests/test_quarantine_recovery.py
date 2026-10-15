import unittest
from unittest.mock import MagicMock, patch

from app.collector.quarantine_recovery import recover_quarantined
from app.collector.windows_inventory import InventoryError


class QuarantineRecoveryTests(unittest.TestCase):
    def run_recovery(self, quarantine, process):
        slot = {'id': 'demo-01', 'executablePath': 'C:/slot/terminal64.exe'}
        with patch('app.collector.quarantine_recovery.InventoryAgent') as agent, \
             patch('app.collector.quarantine_recovery.WindowsTerminal') as native, \
             patch('app.collector.quarantine_recovery.stop_fenced_terminal') as close, \
             patch('app.collector.quarantine_recovery.start_terminal') as start:
            agent.return_value.remote_slots = {'demo-01': {'quarantineIdentity': quarantine}}
            native.return_value.find_process.return_value = process
            start.return_value = True
            result = recover_quarantined({}, 'token', slot)
            return result, close.call_count, start.call_count

    def test_healthy_terminal_is_never_stopped(self):
        self.assertEqual(self.run_recovery(None, (42, '42:1')), ('healthy', 0, 0))

    def test_only_quarantined_identity_is_restarted(self):
        self.assertEqual(self.run_recovery('42:1', (42, '42:1')), ('restarted', 1, 1))

    def test_reused_pid_is_never_stopped(self):
        self.assertEqual(self.run_recovery('42:1', (42, '42:2')), ('replacement_pending_report', 0, 0))

    def test_missing_quarantined_terminal_is_launched(self):
        self.assertEqual(self.run_recovery('42:1', (None, None)), ('restarted', 1, 1))

    def test_busy_mutex_prevents_even_api_refresh(self):
        with patch('app.collector.quarantine_recovery.InventoryAgent') as agent, \
             patch('app.collector.quarantine_recovery.WindowsTerminal') as native:
            native.return_value.inventory_lock.return_value.__enter__.side_effect = RuntimeError('busy')
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                recover_quarantined({}, 'token', {'executablePath': 'C:/slot/terminal64.exe'})
            agent.return_value.connect.assert_not_called()

    def test_failed_fence_check_never_launches_replacement(self):
        with patch('app.collector.quarantine_recovery.InventoryAgent') as agent, \
             patch('app.collector.quarantine_recovery.WindowsTerminal') as native, \
             patch('app.collector.quarantine_recovery.stop_fenced_terminal', side_effect=InventoryError('TERMINAL_CHANGED')), \
             patch('app.collector.quarantine_recovery.start_terminal') as start:
            agent.return_value.remote_slots = {'one': {'quarantineIdentity': '42:1'}}
            native.return_value.find_process.return_value = (42, '42:1')
            with self.assertRaisesRegex(InventoryError, 'TERMINAL_CHANGED'):
                recover_quarantined({}, 'token', {'id': 'one', 'executablePath': 'terminal.exe'})
            start.assert_not_called()
