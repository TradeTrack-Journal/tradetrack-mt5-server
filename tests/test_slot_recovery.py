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
