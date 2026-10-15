from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.collector.server_preparation import stop_fenced_terminal, close_terminal
from app.collector.windows_inventory import InventoryError


class FencedStopTests(TestCase):
    def setUp(self):
        self.native = MagicMock()
        self.native.find_process.return_value = (42, '42:1')
        self.client = MagicMock()
        self.client.call.return_value = {'slots': [{'id': 'demo-01', 'quarantineIdentity': '42:1'}]}
        self.reserve = patch('app.collector.restart_budget.reserve_restart').start()
        self.terminate = patch('app.collector.update_recovery.terminate_verified').start()
        self.defer = patch('app.collector.update_recovery.defer_staged_updates').start()
        self.addCleanup(patch.stopall)

    def stop(self):
        stop_fenced_terminal(self.native, 'terminal.exe', '42:1', self.client, 'demo-01')

    def test_recovery_never_sends_close_or_launches_updater(self):
        self.stop()
        self.client.call.assert_called_once_with('/config')
        self.reserve.assert_called_once_with('terminal.exe', '42:1')
        self.terminate.assert_called_once_with(self.native, 'terminal.exe', 42, '42:1')
        self.defer.assert_called_once_with(self.native, 'terminal.exe')
        self.native.u.PostMessageW.assert_not_called()
        self.native.defer_live_update.assert_not_called()

    def test_removed_or_replaced_fence_preserves_process(self):
        for fence in (None, '42:2'):
            self.client.call.return_value['slots'][0]['quarantineIdentity'] = fence
            with self.assertRaisesRegex(InventoryError, 'TERMINAL_CHANGED'):
                self.stop()
        self.terminate.assert_not_called()
        self.reserve.assert_not_called()
        self.defer.assert_not_called()

    def test_other_slot_fence_does_not_authorize_stop(self):
        self.client.call.return_value['slots'][0]['id'] = 'demo-02'
        with self.assertRaisesRegex(InventoryError, 'TERMINAL_CHANGED'):
            self.stop()
        self.terminate.assert_not_called()

    def test_locked_update_prevents_successful_stop_handoff(self):
        self.defer.side_effect = InventoryError('UPDATE_FILES_BUSY')
        with self.assertRaisesRegex(InventoryError, 'UPDATE_FILES_BUSY'):
            self.stop()
        self.native.u.PostMessageW.assert_not_called()

    def test_reused_pid_is_preserved(self):
        self.native.find_process.return_value = (42, '42:2')
        with self.assertRaisesRegex(InventoryError, 'TERMINAL_CHANGED'):
            self.stop()
        self.terminate.assert_not_called()

    def test_missing_process_does_not_consume_restart_budget(self):
        self.native.find_process.return_value = (None, None)
        self.stop()
        self.reserve.assert_not_called()
        self.terminate.assert_not_called()

    def test_cooldown_is_enforced_before_termination(self):
        self.reserve.side_effect = InventoryError('RECOVERY_COOLDOWN')
        with self.assertRaisesRegex(InventoryError, 'RECOVERY_COOLDOWN'):
            self.stop()
        self.terminate.assert_not_called()

    def test_api_failure_preserves_process(self):
        self.client.call.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            self.stop()
        self.terminate.assert_not_called()

    def test_builder_with_staged_update_does_not_run_elevation_handoff(self):
        with patch('app.collector.server_preparation.staged_update_exists', return_value=True):
            with self.assertRaisesRegex(InventoryError, 'UPDATE_MAINTENANCE_REQUIRED'):
                close_terminal(self.native, 'terminal.exe')
        self.native.u.PostMessageW.assert_not_called()
        self.terminate.assert_not_called()
