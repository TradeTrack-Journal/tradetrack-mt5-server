from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, patch
from app.collector.worker import SlotWorker


class RecoveryTests(TestCase):
    def worker(self, identity):
        worker = object.__new__(SlotWorker)
        worker.slot = {'id': 'demo-02', 'executablePath': 'terminal.exe'}
        worker.restart_identity = 'old'
        worker.windows = MagicMock()
        worker.windows.inventory_lock.return_value = nullcontext()
        worker.windows.find_process.return_value = (12, identity)
        return worker

    def test_only_quarantined_process_restarts(self):
        worker = self.worker('old')
        with patch('app.collector.server_preparation.close_terminal') as close, patch('app.collector.server_preparation.start_terminal') as start:
            self.assertEqual(worker.prepare_inventory()['state'], 'restarting')
            close.assert_called_once_with(worker.windows, 'terminal.exe')
            start.assert_called_once_with('terminal.exe')
        self.assertIsNone(worker.restart_identity)
        self.assertEqual(worker.inspected_at, 0)

    def test_new_process_is_not_stopped(self):
        worker = self.worker('new')
        with patch('app.collector.server_preparation.close_terminal') as close, patch('app.collector.server_preparation.start_terminal') as start:
            worker.prepare_inventory()
            close.assert_not_called()
            start.assert_not_called()

    def test_updater_wait_preserves_quarantine_recovery_intent(self):
        worker = self.worker(None)
        worker.windows.find_process.return_value = (None, None)
        with patch('app.collector.server_preparation.start_terminal', return_value=False):
            self.assertEqual(worker.prepare_inventory()['state'], 'update_pending')
        self.assertEqual(worker.restart_identity, 'old')

    def test_quarantine_recovery_survives_worker_restart(self):
        worker = self.worker(None)
        worker.inventory = MagicMock()
        worker.inventory.remote_slots = {'demo-02': {'quarantineIdentity': 'persisted'}}
        worker.connect()
        self.assertEqual(worker.restart_identity, 'persisted')

    def test_offline_terminal_restarts_once_and_backs_off(self):
        worker = self.worker(None)
        worker.restart_identity = None
        worker.inspected_at = 0
        worker.offline_restart_after = 0
        worker.windows.find_process.return_value = (None, None)
        worker.inventory = MagicMock()
        worker.inventory.remote_slots = {'demo-02': {'quarantineIdentity': None}}
        worker.inventory.report_slot.return_value = {'status': 'OFFLINE', 'errorCode': None}
        with patch('app.collector.server_preparation.start_terminal') as start:
            self.assertEqual(worker.prepare_inventory()['state'], 'restarting')
            self.assertEqual(worker.prepare_inventory()['state'], 'offline')
            start.assert_called_once_with('terminal.exe')

    def test_offline_report_does_not_duplicate_concurrent_launch(self):
        worker = self.worker('new')
        worker.restart_identity = None
        worker.inspected_at = 0
        worker.offline_restart_after = 0
        worker.inventory = MagicMock()
        worker.inventory.remote_slots = {'demo-02': {'quarantineIdentity': None}}
        worker.inventory.report_slot.return_value = {'status': 'OFFLINE', 'errorCode': None}
        with patch('app.collector.server_preparation.start_terminal') as start:
            worker.prepare_inventory()
            start.assert_not_called()

    def test_active_updater_prevents_another_terminal_launch(self):
        from app.collector.server_preparation import start_terminal
        with patch('app.collector.server_preparation.update_running', return_value=True), patch('app.collector.server_preparation.subprocess.Popen') as launch:
            start_terminal('terminal.exe')
            launch.assert_not_called()

    def test_quarantine_arriving_after_connect_is_recovered(self):
        worker = self.worker('old')
        worker.restart_identity = None
        worker.inspected_at = 0
        worker.inventory = MagicMock()
        worker.inventory.remote_slots = {'demo-02': {'quarantineIdentity': 'old'}}
        with patch('app.collector.server_preparation.close_terminal') as close, patch('app.collector.server_preparation.start_terminal'):
            self.assertEqual(worker.prepare_inventory()['state'], 'restarting')
            close.assert_called_once()
        worker.inventory.report_slot.assert_not_called()
