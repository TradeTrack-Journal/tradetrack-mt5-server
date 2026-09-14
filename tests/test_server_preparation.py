from pathlib import Path
from contextlib import nullcontext
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.collector.server_builder import validate_builder
from app.collector.server_preparation import ServerPreparation
from app.collector.windows_inventory import InventoryError


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.preparation = object.__new__(ServerPreparation)
        self.preparation.builder = Path('server-builder/terminal64.exe').resolve()
        self.preparation.native = MagicMock()
        self.preparation.native.find_process.return_value = (123, 'identity')
        self.preparation.retry_at = {}
        self.worker = SimpleNamespace(client=MagicMock(), slot={'id':'test-slot'}, inventory=SimpleNamespace(
            last_snapshots={'test-slot':{'serverNames':['Keep-Manual']}}, sessions={'test-slot':{}}))
        self.worker.client.call.return_value = {'preparationRequests':[{'company':'Example Ltd','serverName':'Example-Demo'}]}

    def test_rejects_collection_slot_as_builder(self):
        with self.assertRaisesRegex(InventoryError, 'DEDICATED_BUILDER_REQUIRED'):
            validate_builder('slots/demo-01/terminal64.exe', 'Example', 'Example-Demo')

    def test_rejects_control_characters(self):
        with self.assertRaisesRegex(InventoryError, 'INVALID_DISCOVERY_REQUEST'):
            validate_builder(self.preparation.builder, 'Example\nInjected', 'Example-Demo')

    def test_no_discovery_when_every_slot_has_server(self):
        self.worker.inventory.last_snapshots['test-slot']['serverNames'].append('Example-Demo')
        self.assertIsNone(self.preparation.once([self.worker]))
        self.preparation.native.search.assert_not_called()

    def test_not_found_never_touches_slot(self):
        self.preparation.native.search.return_value = {'state':'SERVER_NOT_FOUND'}
        self.assertEqual(self.preparation.once([self.worker])['state'], 'SERVER_NOT_FOUND')
        self.worker.client.call.assert_any_call('/preparation-missing', {'serverName':'Example-Demo'})
        self.preparation.native.inventory_lock.assert_not_called()
        self.assertIsNone(self.preparation.once([self.worker]))
        self.preparation.native.search.assert_called_once()

    def test_starting_builder_does_not_delay_discovery_for_five_minutes(self):
        self.preparation.native.search.side_effect = [InventoryError('DISCOVERY_UI_BUSY_0_0'), {'state':'SERVER_NOT_FOUND'}]
        self.assertEqual(self.preparation.once([self.worker])['state'], 'BUILDER_STARTING')
        self.assertEqual(self.preparation.once([self.worker])['state'], 'SERVER_NOT_FOUND')

    def test_manual_servers_cannot_be_lost(self):
        self.preparation.native.search.return_value = {'state':'PRESENT','serverNames':['Example-Demo']}
        with self.assertRaisesRegex(InventoryError, 'BUILDER_CATALOG_INCOMPLETE'):
            self.preparation.once([self.worker])
        self.worker.client.call.assert_called_once_with('/config')
        self.preparation.native.inventory_lock.assert_not_called()

    def test_install_is_fenced_offline_backed_up_and_reinspected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            builder = root / 'server-builder'
            slot = root / 'slots' / 'test-slot'
            for folder, data in [(builder, b'new-catalog'), (slot, b'old-catalog')]:
                (folder / 'config').mkdir(parents=True)
                (folder / 'config' / 'servers.dat').write_bytes(data)
            self.preparation.builder = builder / 'terminal64.exe'
            self.worker.slot.update(executablePath=str(slot / 'terminal64.exe'), dataPath=str(slot))
            self.worker.inventory.sessions['test-slot'] = {'generation':'test-generation','processIdentity':'identity'}
            self.worker.catalog_hash = 'old-hash'
            self.worker.inspected_at = 1
            self.preparation.native.search.return_value = {'state':'PRESENT','serverNames':['Keep-Manual','Example-Demo']}
            self.preparation.native.inventory_lock.side_effect = lambda _: nullcontext()
            stopped = set()
            self.preparation.native.find_process.side_effect = lambda executable: (None, None) if executable in stopped else (123, 'identity')
            def close(native, executable):
                if executable.parent == slot:
                    self.worker.client.call.assert_any_call('/slots/test-slot/prepare', {'generation':'test-generation'})
                    self.assertTrue((slot / '.server-preparing').exists())
                    self.assertEqual((slot / 'config/servers.dat').read_bytes(), b'old-catalog')
                stopped.add(str(executable))
            with patch('app.collector.server_preparation.close_terminal', side_effect=close), patch('app.collector.server_preparation.start_terminal') as start, patch('app.collector.server_preparation.catalogue_fingerprint', return_value='old-hash'):
                result = self.preparation.once([self.worker])
            self.assertEqual(result['state'], 'AWAITING_FRESH_INVENTORY')
            self.assertEqual((slot / 'config/servers.dat').read_bytes(), b'new-catalog')
            backups = list((root / 'server-catalogs').glob('*/*.previous.servers.dat'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'old-catalog')
            self.assertFalse((slot / '.server-preparing').exists())
            self.assertEqual(self.worker.inspected_at, 0)
            start.assert_called_once_with(slot / 'terminal64.exe')


if __name__ == '__main__':
    unittest.main()
