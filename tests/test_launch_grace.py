from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, MagicMock
from app.collector.server_preparation import _launch_terminal
from app.collector.windows_inventory import InventoryError


class LaunchGraceTests(TestCase):
    def test_pending_start_is_not_repeated_by_another_caller(self):
        with TemporaryDirectory() as root, patch('app.collector.server_preparation.time.time', return_value=1000) as clock, \
                patch('app.collector.server_preparation.subprocess.STARTUPINFO', create=True, return_value=MagicMock()), \
                patch('app.collector.server_preparation.subprocess.STARTF_USESHOWWINDOW', 1, create=True), \
                patch('app.collector.server_preparation.subprocess.Popen') as launch:
            path = Path(root) / 'terminal64.exe'
            self.assertTrue(_launch_terminal(path))
            self.assertFalse(_launch_terminal(path))
            launch.assert_called_once()
            clock.return_value = 1060
            self.assertTrue(_launch_terminal(path))
            self.assertEqual(launch.call_count, 2)

    def test_corrupt_launch_marker_cannot_trigger_a_loop(self):
        with TemporaryDirectory() as root, patch('app.collector.server_preparation.subprocess.Popen') as launch:
            (Path(root) / '.terminal-launch.json').write_text('{')
            with self.assertRaisesRegex(InventoryError, 'RECOVERY_BUDGET_UNAVAILABLE'):
                _launch_terminal(Path(root) / 'terminal64.exe')
            launch.assert_not_called()
