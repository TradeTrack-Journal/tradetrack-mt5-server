from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch
from app.collector import update_recovery as recovery


class UpdateRecoveryTests(TestCase):
    def setUp(self):
        recovery._observations.clear()
        recovery._blocked.clear()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / 'liveupdate'
        self.directory.mkdir()
        self.updater = self.directory / 'terminal64.exe'
        self.updater.write_bytes(b'updater')
        self.executable = self.root / 'terminal64.exe'
        self.executable.write_bytes(b'terminal')
        self.native = MagicMock()
        self.native.find_process.side_effect = lambda path: (12, '12:34') if path == str(self.updater) else (None, None)
        self.native.windows.return_value = []
        self.clock = patch.object(recovery.time, 'monotonic', return_value=0).start()
        self.kill = patch.object(recovery, 'terminate_verified').start()
        self.addCleanup(patch.stopall)

    def run_recovery(self):
        return recovery.recover_stalled_update(self.native, self.executable, self.updater, 12, '12:34')

    def test_stable_silent_updater_is_preserved_after_ten_minutes(self):
        self.assertFalse(self.run_recovery())
        self.clock.return_value = 599
        self.assertFalse(self.run_recovery())
        self.clock.return_value = 601
        self.assertTrue(self.run_recovery())
        self.kill.assert_called_once()
        self.assertFalse(self.directory.exists())
        self.assertEqual(len(list(self.root.glob('liveupdate-held-*'))), 1)
        self.assertEqual(self.executable.read_bytes(), b'terminal')

    def test_update_progress_resets_deadline(self):
        self.run_recovery()
        self.clock.return_value = 601
        self.updater.write_bytes(b'new update progress')
        self.assertFalse(self.run_recovery())
        self.kill.assert_not_called()

    def test_visible_updater_is_never_stopped(self):
        self.run_recovery()
        self.clock.return_value = 601
        self.native.windows.return_value = [100]
        self.native.u.IsWindowVisible.return_value = True
        self.assertFalse(self.run_recovery())
        self.kill.assert_not_called()

    def test_live_terminal_is_never_interrupted(self):
        self.run_recovery()
        self.clock.return_value = 601
        self.native.find_process.return_value = (99, '99:88')
        self.native.find_process.side_effect = None
        self.assertFalse(self.run_recovery())
        self.kill.assert_not_called()

    def test_changed_process_resets_deadline(self):
        self.run_recovery()
        self.clock.return_value = 601
        self.assertFalse(recovery.recover_stalled_update(self.native, self.executable, self.updater, 13, '13:35'))
        self.kill.assert_not_called()

    def test_persistent_visible_updater_alerts_without_killing_it(self):
        self.native.windows.return_value = [100]
        self.native.u.IsWindowVisible.return_value = True
        self.assertFalse(self.run_recovery())
        self.clock.return_value = 601
        with self.assertRaisesRegex(recovery.InventoryError, 'UPDATE_REQUIRES_ATTENTION'):
            self.run_recovery()
        self.kill.assert_not_called()

    def test_package_blocks_launch_even_if_updater_process_is_not_visible(self):
        import os
        from app.collector.server_preparation import update_running
        terminal_root = self.root / 'MetaQuotes/Terminal/hash'
        terminal_root.mkdir(parents=True)
        (terminal_root / 'origin.txt').write_text(str(self.executable.parent), encoding='utf-8')
        package = terminal_root / 'liveupdate'
        package.mkdir()
        (package / 'terminal64.exe').write_bytes(b'updater')
        with patch.dict(os.environ, {'APPDATA': str(self.root)}), patch('app.collector.server_preparation.WindowsTerminal') as native:
            native.return_value.find_process.return_value = (None, None)
            self.assertTrue(update_running(self.executable))
            self.clock.return_value = 601
            with self.assertRaisesRegex(recovery.InventoryError, 'UPDATE_PROCESS_UNVERIFIED'):
                update_running(self.executable)
            self.kill.assert_not_called()
