import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from app.collector.archive_storage import candidates, deduplicate


class ArchiveStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'Terminal'
        self.store = self.base / 'store'
        self.root.mkdir()

    def tearDown(self):
        for path in self.base.rglob('*'):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        self.temp.cleanup()

    def package(self, archive, data=b'vendor bytes', profile='A' * 32, name='mt5clw64.6316'):
        folder = self.root / profile / archive
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(data)
        old = time.time() - 7200
        os.utime(path, (old, old))
        os.utime(folder, (old, old))
        return path

    def test_duplicate_paths_and_hashes_preserved_and_second_pass_idempotent(self):
        a = self.package('liveupdate-deferred-1000000000000000001')
        b = self.package('liveupdate-orphan-held-1000000000000000002', profile='B' * 32)
        unique = self.package('liveupdate-held-20260921', b'different vendor bytes')
        original = {p: p.read_bytes() for p in (a, b, unique)}
        result = deduplicate(self.root, self.store)
        self.assertEqual(result['linked'], 1)
        self.assertEqual(result['newObjects'], 2)
        self.assertTrue(os.path.samefile(a, b))
        self.assertFalse(os.path.samefile(a, unique))
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)
        again = deduplicate(self.root, self.store)
        self.assertEqual(again['alreadyLinked'], 3)
        self.assertEqual(again['linked'], 0)

    def test_live_recent_account_and_unknown_files_are_untouched(self):
        live = self.package('liveupdate')
        history = self.package('bases', name='history.dat')
        unknown = self.package('liveupdate-held-1000000000000000003', name='accounts.dat')
        recent = self.package('liveupdate-deferred-1000000000000000004')
        os.utime(recent, None)
        inode = {p: p.stat().st_ino for p in (live, history, unknown, recent)}
        self.assertEqual(list(candidates(self.root, time.time())), [])
        deduplicate(self.root, self.store)
        self.assertEqual(inode, {p: p.stat().st_ino for p in inode})

    def test_corrupt_existing_object_does_not_replace_source(self):
        source = self.package('liveupdate-deferred-1000000000000000001')
        key = hashlib.sha256(source.read_bytes()).hexdigest()
        objects = self.store / 'objects'
        objects.mkdir(parents=True)
        (objects / key).write_bytes(b'corrupt')
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
            deduplicate(self.root, self.store)
        self.assertEqual(source.read_bytes(), before)

    def test_replace_failure_preserves_original_and_canonical(self):
        a = self.package('liveupdate-deferred-1000000000000000001')
        b = self.package('liveupdate-deferred-1000000000000000002')
        with patch('app.collector.archive_storage.replace_archive_link', side_effect=OSError('busy')):
            result = deduplicate(self.root, self.store)
        self.assertEqual(result['skippedBusy'], 1)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertFalse(os.path.samefile(a, b))

    def test_budget_skips_already_linked_without_starving_later_archives(self):
        a = self.package('liveupdate-deferred-1000000000000000001')
        deduplicate(self.root, self.store, max_files=1)
        b = self.package('liveupdate-deferred-1000000000000000002')
        result = deduplicate(self.root, self.store, max_files=1)
        self.assertEqual(result['linked'], 1)
        self.assertTrue(os.path.samefile(a, b))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing contract')
    def test_open_writer_is_skipped(self):
        from app.collector.update_recovery import exclusive_update_files
        source = self.package('liveupdate-deferred-1000000000000000001')
        with exclusive_update_files([source]):
            result = deduplicate(self.root, self.store)
        self.assertEqual(result['skippedBusy'], 1)
        self.assertEqual(source.read_bytes(), b'vendor bytes')

    def test_overlapping_store_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'OVERLAP'):
            deduplicate(self.root, self.root / 'objects')
