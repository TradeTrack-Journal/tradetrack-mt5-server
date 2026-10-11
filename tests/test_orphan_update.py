from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch
import os

from app.collector import update_recovery as recovery


class OrphanUpdateTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'liveupdate'
        self.directory.mkdir()
        self.exe = self.root / 'terminal64.exe'
        self.updater = self.directory / 'terminal64.exe'
        for p in (self.exe, self.updater, self.directory / 'package.6252'):
            p.write_bytes(b'preserved')
            os.utime(p, (1000, 1000))
        self.native = MagicMock()
        self.native.find_process.return_value = (None, None)
        recovery._orphans.clear()
        self.clock = patch.object(recovery.time, 'monotonic', return_value=0).start()
        patch.object(recovery.time, 'time', return_value=2000).start()
        self.access = patch.object(recovery, 'exclusive_update_files', side_effect=lambda _: nullcontext()).start()
        self.addCleanup(patch.stopall)

    def recover(self):
        return recovery.recover_orphaned_update(self.native, self.exe, self.updater)

    def test_orphan_is_archived_after_stable_observations_not_left_blocked(self):
        self.assertFalse(self.recover())
        self.clock.return_value = 61
        self.assertTrue(self.recover())
        self.assertFalse(self.updater.exists())
        held = list(self.root.glob('liveupdate-orphan-held-*'))
        self.assertEqual(len(held), 1)
        self.assertEqual((held[0] / 'terminal64.exe').read_bytes(), b'preserved')
        self.assertEqual((held[0] / 'package.6252').read_bytes(), b'preserved')
        self.assertEqual(self.exe.read_bytes(), b'preserved')

    def test_active_or_inaccessible_updater_cannot_be_archived(self):
        self.recover(); self.clock.return_value = 61
        self.native.find_process.return_value = (99, '99:1')
        self.assertFalse(self.recover())
        self.access.assert_not_called()
        self.native.find_process.return_value = (None, None)
        self.recover(); self.clock.return_value = 122
        self.access.side_effect = recovery.InventoryError('UPDATE_FILES_BUSY')
        with self.assertRaisesRegex(recovery.InventoryError, 'UPDATE_FILES_BUSY'):
            self.recover()
        self.assertTrue(self.updater.exists())

    def test_modified_or_recent_files_reset_or_delay_recovery(self):
        self.recover(); self.clock.return_value = 61
        self.updater.write_bytes(b'progress')
        self.assertFalse(self.recover())
        self.clock.return_value = 122
        self.assertFalse(self.recover())  # Fresh mtime, even if content is stable.
        self.access.assert_not_called()

    def test_process_appearing_during_exclusive_check_is_preserved(self):
        self.recover(); self.clock.return_value = 61
        self.native.find_process.side_effect = [(None, None), (None, None), (99, '99:1')]
        self.assertFalse(self.recover())
        self.assertTrue(self.updater.exists())

    def test_partial_archive_keeps_updater_marker_until_other_files_are_safe(self):
        self.recover(); self.clock.return_value = 61
        real_rename = Path.rename
        def fail_package(path, destination):
            if path.name == 'package.6252':
                raise PermissionError('locked')
            return real_rename(path, destination)
        with patch.object(Path, 'rename', fail_package):
            with self.assertRaises(PermissionError):
                self.recover()
        self.assertTrue(self.updater.exists())
