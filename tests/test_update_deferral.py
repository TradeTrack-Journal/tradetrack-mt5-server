from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.collector import update_recovery as recovery


class UpdateDeferralTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.slot = self.root / 'slot'
        self.slot.mkdir()
        self.exe = self.slot / 'terminal64.exe'
        self.exe.write_bytes(b'installed')
        self.profile = self.root / 'MetaQuotes' / 'Terminal' / 'origin'
        self.directory = self.profile / 'liveupdate'
        self.directory.mkdir(parents=True)
        (self.profile / 'origin.txt').write_text(str(self.slot), encoding='utf-16')
        (self.directory / 'terminal64.exe').write_bytes(b'updater')
        (self.directory / 'mt5clw64.6259').write_bytes(b'package')
        self.native = MagicMock()
        self.native.find_process.return_value = (None, None)
        patch.dict(recovery.os.environ, APPDATA=str(self.root)).start()
        self.access = patch.object(recovery, 'exclusive_update_files', return_value=nullcontext()).start()
        self.addCleanup(patch.stopall)

    def defer(self):
        recovery.defer_staged_updates(self.native, self.exe)

    def test_pending_update_preserved_and_installed_binary_untouched(self):
        self.defer()
        archived = list(self.profile.glob('liveupdate-deferred-*'))
        self.assertEqual(len(archived), 1)
        self.assertEqual((archived[0] / 'mt5clw64.6259').read_bytes(), b'package')
        self.assertEqual((archived[0] / 'terminal64.exe').read_bytes(), b'updater')
        self.assertEqual(self.exe.read_bytes(), b'installed')
        self.assertFalse((self.directory / 'terminal64.exe').exists())
        self.defer()  # Idempotent when an earlier fenced stop already deferred files.
        self.assertEqual(len(list(self.profile.glob('liveupdate-deferred-*'))), 1)

    def test_running_main_or_updater_is_not_touched(self):
        for values in ([(42, '42:1')], [(None, None), (43, '43:1')]):
            self.native.find_process.side_effect = values
            with self.assertRaises(recovery.InventoryError):
                self.defer()
            self.assertTrue((self.directory / 'terminal64.exe').exists())
        self.access.assert_not_called()

    def test_inaccessible_updater_or_package_is_preserved(self):
        self.access.side_effect = recovery.InventoryError('UPDATE_FILES_BUSY')
        with self.assertRaisesRegex(recovery.InventoryError, 'UPDATE_FILES_BUSY'):
            self.defer()
        self.assertTrue((self.directory / 'terminal64.exe').exists())

    def test_process_appearing_after_file_locks_is_preserved(self):
        self.native.find_process.side_effect = [(None, None), (None, None), (42, '42:1')]
        with self.assertRaisesRegex(recovery.InventoryError, 'UPDATE_FILES_BUSY'):
            self.defer()
        self.assertTrue((self.directory / 'terminal64.exe').exists())

    def test_other_origin_is_not_touched(self):
        (self.profile / 'origin.txt').write_text(str(self.root / 'unrelated'), encoding='utf-8')
        self.defer()
        self.assertTrue((self.directory / 'terminal64.exe').exists())
        self.access.assert_not_called()

    def test_partial_archive_keeps_executable_marker(self):
        rename = Path.rename
        def fail_package(path, target):
            if path.name.endswith('.6259'):
                raise PermissionError('locked')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_package):
            with self.assertRaises(PermissionError):
                self.defer()
        self.assertTrue((self.directory / 'terminal64.exe').exists())
