from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.collector.gateway import investor_session_evidence, journal_baseline, collect, CollectionError
from tests import test_worker


class SessionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'logs').mkdir()
        self.path = self.root/'logs'/'20261120.log'
        self.path.write_bytes(b'')
        self.baseline = journal_baseline(self.root)
        self.start = datetime(2026,11,20,10,0,0)

    def append(self, message, stamp='10:00:01.000', source='Network', newline=True):
        with self.path.open('ab') as f:
            f.write(('AB\t0\t'+stamp+'\t'+source+'\t'+message+('\r\n' if newline else '')).encode('utf-16-le'))

    def auth(self, login='12345', server='Bybit-Live-7', **kwargs):
        self.append(f"'{login}': authorized on {server} through AS01", **kwargs)

    def investor(self, login='12345', **kwargs):
        self.append(f"'{login}': trading has been disabled - investor mode", **kwargs)

    def verified(self):
        return investor_session_evidence(self.root,self.baseline,'12345','Bybit-Live-7',self.start)

    def test_requires_current_exact_authorization_before_investor(self):
        self.investor()
        self.assertFalse(self.verified())
        self.auth()
        self.assertFalse(self.verified())
        self.investor()
        self.assertTrue(self.verified())

    def test_wrong_account_and_server_cannot_authorize(self):
        for login,server in [('12346','Bybit-Live-7'),('12345','Bybit-Live-6'),('12345','Bybit-Live-70')]:
            self.auth(login,server)
            self.investor()
            self.assertFalse(self.verified())

    def test_wrong_source_or_investor_login_is_rejected(self):
        self.auth(source='Expert')
        self.investor()
        self.assertFalse(self.verified())
        self.auth()
        self.investor('99999')
        self.assertFalse(self.verified())

    def test_buffered_prior_session_after_baseline_is_rejected(self):
        self.auth(stamp='09:59:59.999')
        self.investor()
        self.assertFalse(self.verified())

    def test_records_already_on_disk_are_rejected(self):
        self.auth()
        self.investor()
        self.baseline=journal_baseline(self.root)
        self.assertFalse(self.verified())

    def test_later_session_changes_invalidate_positive_evidence(self):
        for message in ["'12345': disconnected from Bybit-Live-7",
                        "'12345': trading has been enabled - hedging mode",
                        "'12345': authorized on Bybit-Live-6 through AS01",
                        "'99999': authorized on Bybit-Live-7 through AS01",
                        "'12345': authorization on Bybit-Live-7 failed (Invalid account)"]:
            self.auth()
            self.investor()
            self.assertTrue(self.verified())
            self.append(message)
            self.assertFalse(self.verified())

    def test_reauthorization_requires_new_mode_evidence(self):
        self.auth()
        self.investor()
        self.auth()
        self.assertFalse(self.verified())
        self.investor()
        self.assertTrue(self.verified())

    def test_incomplete_record_never_authorizes(self):
        self.auth()
        self.investor(newline=False)
        self.assertFalse(self.verified())

    def test_rotated_or_truncated_file_is_rejected(self):
        self.auth()
        self.investor()
        self.baseline=journal_baseline(self.root)
        self.path.write_bytes(b'')
        self.assertFalse(self.verified())

    def test_missing_baseline_fails_closed(self):
        self.auth()
        self.investor()
        self.baseline=None
        self.assertFalse(self.verified())


class GatewayJournalTests(unittest.TestCase):
    setUp = test_worker.GatewayTests.setUp

    def test_fresh_journal_can_replace_unavailable_caption(self):
        self.windows.read_only_session.return_value=False
        with patch('app.collector.gateway.investor_session_evidence',return_value=True):
            result=collect(self.request,self.native,self.windows)
        self.assertTrue(result['investorVerified'])
        self.assertEqual(result['investorVerificationMethod'],'terminal_journal_read_only')
        self.native.order_send.assert_not_called()

    def test_journal_cannot_override_trading_permissions(self):
        self.windows.read_only_session.return_value=False
        self.native.account_info.return_value.trade_allowed=True
        with patch('app.collector.gateway.investor_session_evidence',return_value=True):
            with self.assertRaisesRegex(CollectionError,'TRADING_ENABLED'):
                collect(self.request,self.native,self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_journal_cannot_admit_unknown_build(self):
        self.windows.read_only_session.return_value=False
        self.native.terminal_info.return_value.build=9999
        with patch('app.collector.gateway.investor_session_evidence',return_value=True), patch('app.collector.gateway.time.monotonic',side_effect=[0,11]):
            with self.assertRaisesRegex(CollectionError,'INVESTOR_UNVERIFIED'):
                collect(self.request,self.native,self.windows)
        self.native.history_deals_get.assert_not_called()

    def test_session_loss_during_read_discards_snapshot(self):
        self.windows.read_only_session.return_value=False
        with patch('app.collector.gateway.investor_session_evidence',side_effect=[True,False]):
            with self.assertRaisesRegex(CollectionError,'INVESTOR_UNVERIFIED'):
                collect(self.request,self.native,self.windows)
        self.native.positions_get.assert_not_called()
