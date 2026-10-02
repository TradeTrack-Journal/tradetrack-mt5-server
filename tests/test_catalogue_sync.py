from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.collector.catalogue_sync import choose_pair, install_catalogue, synchronize_once
from app.collector.node_agent import AgentError
from app.collector.windows_inventory import InventoryError


def worker(root, name, names, data):
    folder = root / 'slots' / name
    (folder / 'config').mkdir(parents=True)
    (folder / 'config' / 'servers.dat').write_bytes(data)
    (folder / 'config' / 'accounts.dat').write_bytes(b'private-do-not-copy')
    digest = sha256(data).hexdigest()
    native = MagicMock()
    native.inventory_lock.side_effect = lambda _: nullcontext()
    native.find_process.return_value = (12, '12:34')
    native.process_identity.return_value = '12:34'
    native.terminal_build.return_value = 6231
    snapshot = {'serverNames': names, 'status': 'STARTING', 'verificationMethod': 'login_dialog',
                'catalogHash': digest, 'processIdentity': '12:34'}
    return SimpleNamespace(slot={'id': name, 'dataPath': str(folder), 'executablePath': str(folder / 'terminal64.exe')},
                           windows=native, client=MagicMock(), inspected_at=time.monotonic(), catalog_hash=digest,
                           inventory=SimpleNamespace(last_snapshots={name: snapshot}, sessions={name: {'generation': 'generation', 'processIdentity': '12:34'}}))


class CatalogueSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = worker(self.root, 'demo-01', ['Existing', 'OctaFX-Real'], b'new')
        self.target = worker(self.root, 'demo-02', ['Existing'], b'old')

    def test_no_names_are_removed_or_equal_catalogues_restarted(self):
        self.assertIsNotNone(choose_pair([self.source, self.target]))
        self.target.inventory.last_snapshots['demo-02']['serverNames'] = ['Unique']
        self.assertIsNone(choose_pair([self.source, self.target]))
        self.target.inventory.last_snapshots['demo-02']['serverNames'] = ['Existing', 'OctaFX-Real']
        self.assertIsNone(choose_pair([self.source, self.target]))

    def test_stale_or_failed_inventory_cannot_propagate(self):
        self.source.inspected_at = time.monotonic() - 91
        self.assertIsNone(choose_pair([self.source, self.target]))
        self.source.inspected_at = time.monotonic()
        self.source.inventory.last_snapshots['demo-01']['verificationMethod'] = 'none'
        self.assertIsNone(choose_pair([self.source, self.target]))

    def test_source_rewrite_rejected_before_target_is_fenced(self):
        Path(self.source.slot['dataPath'], 'config/servers.dat').write_bytes(b'changed')
        with self.assertRaisesRegex(InventoryError, 'CATALOGUE_CHANGED'):
            synchronize_once([self.source, self.target])
        self.target.client.call.assert_not_called()

    def test_build_mismatch_does_not_stop_target(self):
        self.target.windows.terminal_build.return_value = 6230
        with self.assertRaisesRegex(InventoryError, 'CATALOGUE_BUILD_MISMATCH'):
            synchronize_once([self.source, self.target])
        self.target.client.call.assert_not_called()
        self.assertEqual(Path(self.target.slot['dataPath'], 'config/servers.dat').read_bytes(), b'old')

    def test_busy_api_fence_prevents_shutdown_and_writes(self):
        self.target.client.call.side_effect = AgentError('API_HTTP_409')
        with patch('app.collector.catalogue_sync.close_terminal') as close:
            with self.assertRaisesRegex(AgentError, 'API_HTTP_409'):
                synchronize_once([self.source, self.target])
        close.assert_not_called()
        self.assertEqual(Path(self.target.slot['dataPath'], 'config/servers.dat').read_bytes(), b'old')

    def test_restart_backup_and_no_credential_copy(self):
        self.target.windows.find_process.side_effect = [(12, '12:34'), (None, None)]
        def close(native, executable):
            self.target.client.call.assert_called_once_with('/slots/demo-02/prepare', {'generation': 'generation'})
            self.assertEqual(Path(self.target.slot['dataPath'], 'config/servers.dat').read_bytes(), b'old')
        with patch('app.collector.catalogue_sync.close_terminal', side_effect=close), \
                patch('app.collector.catalogue_sync.start_terminal', return_value=True):
            result = synchronize_once([self.source, self.target])
        self.assertEqual(result['addedServers'], 1)
        self.assertEqual(result['state'], 'CATALOGUE_SYNC_PENDING_VERIFICATION')
        self.assertEqual(self.target.inspected_at, 0)
        self.assertEqual(Path(self.target.slot['dataPath'], 'config/servers.dat').read_bytes(), b'new')
        self.assertEqual(Path(self.target.slot['dataPath'], 'config/accounts.dat').read_bytes(), b'private-do-not-copy')
        backups = list((self.root / 'server-catalogs').glob('*/*.previous.servers.dat'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'old')

    def test_missing_target_names_fail_before_any_file_write(self):
        with self.assertRaisesRegex(InventoryError, 'CATALOGUE_WOULD_REMOVE_SERVERS'):
            install_catalogue(self.target, b'new', {'OctaFX-Real'}, 6231)
        self.target.client.call.assert_not_called()
        self.assertFalse((self.root / 'server-catalogs').exists())
