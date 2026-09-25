from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.collector.gateway import collect, CollectionError, investor_evidence, journal_baseline
from app.collector.worker import run_child, SlotWorker
from app.collector.windows_inventory import read_only_caption_matches
from app.collector.normalization import normalize_account
from app.collector.contracts import DataError


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.request = {"slot": {"executablePath": str(Path('terminal64.exe').resolve()), "dataPath": str(Path('.').resolve())},
                        "processId": 12, "processIdentity": "12:34",
                        "credentials": {"login": "12345", "serverName": "Example-Demo", "investorPassword": "test-only"}}
        self.native = MagicMock()
        self.native.initialize.return_value = self.native.login.return_value = True
        self.native.account_info.return_value = SimpleNamespace(login=12345, server='Example-Demo', trade_allowed=False, balance=100084.45, equity=100090.12, currency='USD')
        self.native.terminal_info.return_value = SimpleNamespace(path=str(Path('.').resolve()), data_path=str(Path('.').resolve()), connected=True, tradeapi_disabled=True, build=6182)
        self.native.history_deals_get.return_value = ()
        self.native.history_deals_total.return_value = 0
        self.native.positions_get.return_value = ()
        self.windows = MagicMock()
        self.windows.process_identity.return_value = '12:34'
        self.windows.read_only_session.return_value = True
        for name in ('validate_data_path', 'journal_baseline', 'investor_evidence', 'time.sleep'):
            p = patch('app.collector.gateway.' + name, return_value=True)
            p.start()
            self.addCleanup(p.stop)

    def test_success_empty_keeps_completeness_unverified(self):
        result = collect(self.request, self.native, self.windows)
        self.assertFalse(result['historyCompletenessVerified'])
        self.assertTrue(result['rangeCountVerified'])
        self.assertTrue(result['olderHistoryEmpty'])
        self.assertEqual(result['deals'], [])
        self.assertEqual(result['account'], {'balance': '100084.45', 'equity': '100090.12', 'currency': 'USD'})
        self.native.shutdown.assert_called_once()
        self.native.order_send.assert_not_called()
        self.assertNotIn('investorPassword', self.request['credentials'])

    def test_count_change_does_not_advance_history(self):
        self.native.history_deals_total.side_effect = [0, 1]
        with self.assertRaisesRegex(CollectionError, 'HISTORY_UNAVAILABLE'):
            collect(self.request, self.native, self.windows)

    def test_verified_6190_is_not_reported_as_6182(self):
        self.native.terminal_info.return_value.build = 6190
        result = collect(self.request, self.native, self.windows)
        self.assertEqual(result['terminalBuild'], 6190)
        self.assertEqual(self.windows.read_only_session.call_args.args[-1], 6190)

    def test_build_change_during_collection_discards_result(self):
        initial = self.native.terminal_info.return_value
        changed = SimpleNamespace(**{**vars(initial), 'build': 6190})
        self.native.terminal_info.side_effect = [initial, changed]
        with self.assertRaisesRegex(CollectionError, 'IDENTITY_DRIFT'):
            collect(self.request, self.native, self.windows)

    def test_backfill_window_keeps_requested_upper_cursor(self):
        self.request['credentials']['historyBeforeMs'] = 1700000000000
        result = collect(self.request, self.native, self.windows)
        self.assertEqual(result['windowEndMs'], 1700000000000)
        self.assertEqual(result['backfillBeforeMs'], 1700000000000)
        self.assertLess(result['windowStartMs'], result['windowEndMs'])

    def test_dense_backfill_is_bounded(self):
        self.request['credentials']['historyBeforeMs'] = 1700000000000
        self.native.history_deals_total.return_value = 5001
        with self.assertRaisesRegex(CollectionError, 'BATCH_TOO_LARGE'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_read_permission_is_required_even_with_investor_journal(self):
        self.native.account_info.return_value.trade_allowed = True
        with self.assertRaisesRegex(CollectionError, 'TRADING_ENABLED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_consented_trading_password_reads_without_trading(self):
        self.native.account_info.return_value.trade_allowed = True
        self.windows.read_only_session.return_value = False
        self.request['credentials']['allowTradingPassword'] = True
        result = collect(self.request, self.native, self.windows)
        self.assertFalse(result['investorVerified'])
        self.assertEqual(result['investorVerificationMethod'], 'trading_password_consent')
        self.native.order_send.assert_not_called()
        self.native.order_check.assert_not_called()

    def test_consent_allows_unverified_caption_without_claiming_investor_mode(self):
        self.windows.read_only_session.return_value = False
        self.request['credentials']['allowTradingPassword'] = True
        result = collect(self.request, self.native, self.windows)
        self.assertFalse(result['investorVerified'])
        self.assertEqual(result['investorVerificationMethod'], 'trading_password_consent')
        self.assertTrue(result['pythonTradingDisabled'])

    def test_consent_requires_boolean_true(self):
        self.native.account_info.return_value.trade_allowed = True
        self.request['credentials']['allowTradingPassword'] = 'true'
        with self.assertRaisesRegex(CollectionError, 'TRADING_ENABLED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_consent_does_not_allow_python_trading(self):
        self.native.account_info.return_value.trade_allowed = True
        self.native.terminal_info.return_value.tradeapi_disabled = False
        self.request['credentials']['allowTradingPassword'] = True
        with self.assertRaisesRegex(CollectionError, 'PYTHON_TRADING_ENABLED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_consent_does_not_allow_server_mismatch(self):
        self.native.account_info.return_value.trade_allowed = True
        self.native.account_info.return_value.server = 'Wrong'
        self.request['credentials']['allowTradingPassword'] = True
        with self.assertRaisesRegex(CollectionError, 'IDENTITY_DRIFT'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_python_trading_must_be_disabled(self):
        self.native.terminal_info.return_value.tradeapi_disabled = False
        with self.assertRaisesRegex(CollectionError, 'PYTHON_TRADING_ENABLED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_login_false_never_reads_cached_data(self):
        self.native.login.return_value = False
        with self.assertRaisesRegex(CollectionError, 'CONNECTION_FAILED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_pid_reuse_prevents_initialize(self):
        self.windows.process_identity.return_value = '12:99'
        with self.assertRaisesRegex(CollectionError, 'TERMINAL_CHANGED'):
            collect(self.request, self.native, self.windows)
        self.native.initialize.assert_not_called()

    def test_different_server_and_paths_are_rejected(self):
        self.native.account_info.return_value.server = 'Other-Demo'
        with self.assertRaisesRegex(CollectionError, 'IDENTITY_DRIFT'):
            collect(self.request, self.native, self.windows)

    def test_none_history_and_positions_are_errors(self):
        self.native.history_deals_get.return_value = None
        with self.assertRaisesRegex(CollectionError, 'HISTORY_UNAVAILABLE'):
            collect(self.request, self.native, self.windows)
        self.request['credentials']['investorPassword'] = 'test-only'
        self.native.history_deals_get.return_value = ()
        self.native.positions_get.return_value = None
        with self.assertRaisesRegex(CollectionError, 'POSITIONS_UNAVAILABLE'):
            collect(self.request, self.native, self.windows)

    def test_unknown_investor_mode_fails_closed(self):
        self.windows.read_only_session.return_value = False
        with patch('app.collector.gateway.time.monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(CollectionError, 'INVESTOR_UNVERIFIED'):
                collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_initial_caption_can_settle_before_any_history_read(self):
        attempts = []
        def evidence(*args):
            attempts.append(True)
            if len(attempts) == 1:
                self.native.history_deals_get.assert_not_called()
                return False
            return True
        self.windows.read_only_session.side_effect = evidence
        self.assertTrue(collect(self.request, self.native, self.windows)['investorVerified'])

    def test_caption_wait_does_not_tolerate_trading_permission_change(self):
        self.windows.read_only_session.return_value = False
        initial = self.native.account_info.return_value
        changed = SimpleNamespace(**{**vars(initial), 'trade_allowed': True})
        self.native.account_info.side_effect = [initial, changed]
        with self.assertRaisesRegex(CollectionError, 'TRADING_ENABLED'):
            collect(self.request, self.native, self.windows)
        self.native.history_deals_get.assert_not_called()


class WorkerTests(unittest.TestCase):
    def test_collection_thread_requests_main_thread_maintenance_without_ui(self):
        worker = object.__new__(SlotWorker)
        worker.restart_identity = None
        worker.slot = {'id': 'demo-test'}
        worker.inspected_at = 0
        worker.prepare_inventory = MagicMock(side_effect=AssertionError('UI must stay on main thread'))
        self.assertEqual(worker.once(allow_ui=False), {'slotId': 'demo-test', 'state': 'maintenance_required'})
        worker.prepare_inventory.assert_not_called()

    def test_account_balance_zero_and_negative_are_preserved(self):
        value = normalize_account({'balance': 0, 'equity': -2.75, 'currency': 'USD'})
        self.assertEqual((value.balance, value.equity), ('0', '-2.75'))

    def test_invalid_account_snapshot_never_becomes_zero_balance(self):
        for field, invalid in [('balance', None), ('balance', float('nan')), ('equity', float('inf')), ('currency', ''), ('currency', 'USD\n')]:
            row = {'balance': 100, 'equity': 100, 'currency': 'USD', field: invalid}
            with self.assertRaises(DataError):
                normalize_account(row)
    def test_caption_requires_account_server_mode_build_and_supported_language(self):
        good = '12345 - Example-Demo: Demo Account - Read Only - Hedge - Example Ltd.'
        self.assertTrue(read_only_caption_matches(good, '12345', 'Example-Demo', 6182))
        self.assertTrue(read_only_caption_matches(good, '12345', 'Example-Demo', 6190))
        self.assertTrue(read_only_caption_matches(good, '12345', 'Example-Demo', 6193))
        self.assertTrue(read_only_caption_matches(good.replace('Hedge', 'Netting'), '12345', 'Example-Demo', 6182))
        for title, login, server, build in [
            (good, '99999', 'Example-Demo', 6182), (good, '12345', 'Other', 6182),
            (good, '12345', 'Example-Demo', 6183),
            (good, '12345', 'Example-Demo', 6191),
            (good.replace('Read Only - ', ''), '12345', 'Example-Demo', 6193),
            (good, '99999', 'Example-Demo', 6193),
            (good, '12345', 'Other', 6193),
            (good.replace('Hedge', 'Netting'), '12345', 'Example-Demo', 6193),
            (good.replace('Hedge', 'Netting'), '12345', 'Example-Demo', 6190),
            (good, '12345', 'Example-Demo', '6190'),
            (good.replace('Read Only - ', ''), '12345', 'Example-Demo', 6190),
            (good.replace('Demo Account', 'Real Account'), '12345', 'Example-Demo', 6190),
            (good.replace('Read Only', 'Тільки читання'), '12345', 'Example-Demo', 6190),
            (good, '99999', 'Example-Demo', 6190),
            (good, '12345', 'Other', 6190),
            (good.replace('Read Only - ', ''), '12345', 'Example-Demo', 6182),
            ('12345 - Example-Demo: Demo Account - Hedge - Read Only - Example Ltd.', '12345', 'Example-Demo', 6182),
            (good.replace('Read Only', 'Тільки читання'), '12345', 'Example-Demo', 6182),
        ]:
            self.assertFalse(read_only_caption_matches(title, login, server, build))

    def test_only_fresh_journal_proves_investor(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'logs'
            log.mkdir()
            path = log / '20260910.log'
            line = "EN\t0\t17:00:00.000\tNetwork\t'12345': trading has been disabled - investor mode\r\n"
            path.write_text(line, encoding='utf-16')
            baseline = journal_baseline(root)
            self.assertFalse(investor_evidence(root, baseline, '12345'))
            with path.open('ab') as stream:
                stream.write(line.encode('utf-16-le'))
            self.assertTrue(investor_evidence(root, baseline, '12345'))
            self.assertFalse(investor_evidence(root, baseline, '99999'))

    def test_closed_terminal_is_reported_without_claim(self):
        worker = object.__new__(SlotWorker)
        worker.restart_identity = None
        worker.offline_restart_after = time.monotonic() + 60
        worker.slot = {'id': 'closed'}
        worker.inventory = MagicMock()
        worker.inventory.remote_slots = {'closed': {'quarantineIdentity': None}}
        worker.inventory.report_slot.return_value = {'status': 'OFFLINE', 'errorCode': None}
        worker.inspected_at = 0
        worker.client = MagicMock()
        self.assertEqual(worker.once()['state'], 'offline')
        worker.client.call.assert_not_called()

    def test_real_hung_child_is_killed_without_secret_output(self):
        actual = subprocess.Popen
        launched = []
        def launch(*args, **kwargs):
            process = actual([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            launched.append(process)
            return process
        payload = {'credentials': {'investorPassword': 'never-print-this'}}
        with patch('app.collector.worker.subprocess.Popen', side_effect=launch):
            result = run_child(payload, MagicMock(), timeout=0.2)
        self.assertEqual(result, {'errorCode': 'CHILD_TIMEOUT'})
        self.assertIsNotNone(launched[0].poll())
        self.assertNotIn('investorPassword', payload['credentials'])

    def test_blocked_http_heartbeat_cannot_block_child_watchdog(self):
        actual = subprocess.Popen
        launched = []
        def launch(*args, **kwargs):
            process = actual([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            launched.append(process)
            return process
        started = time.monotonic()
        with patch('app.collector.worker.subprocess.Popen', side_effect=launch):
            result = run_child({'credentials': {'investorPassword': 'test-only'}}, lambda: time.sleep(1), timeout=0.25, heartbeat_interval=0.01)
        self.assertEqual(result['errorCode'], 'CHILD_TIMEOUT')
        self.assertIsNotNone(launched[0].poll())
        self.assertLess(time.monotonic() - started, 0.9)


if __name__ == '__main__':
    unittest.main()
