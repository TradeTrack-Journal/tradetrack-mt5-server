from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.collector.gateway import journal_baseline, login_failure


class LoginFailureTests(unittest.TestCase):
    def test_partial_service_record_and_utf16_character_are_not_auth_rejections(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        record = "CN\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Service is not available)\r\n".encode('utf-16-le')
        for split in [len(record) - 20, len(record) - 19, len(record) - 4]:
            with self.subTest(split=split), tempfile.TemporaryDirectory() as root:
                folder = Path(root) / 'logs'; folder.mkdir()
                path = folder / '20261003.log'; path.write_bytes(b'\xff\xfe')
                baseline = journal_baseline(root)
                with path.open('ab') as out:
                    out.write(record[:split])
                def finish(_):
                    with path.open('ab') as out:
                        out.write(record[split:])
                with patch('app.collector.gateway.time.sleep', side_effect=finish) as sleep:
                    self.assertEqual(login_failure(native, root, baseline, '12345', 'Example-Real'), 'NETWORK_UNAVAILABLE')
                sleep.assert_called_once()
                native.login.assert_not_called()
                native.history_deals_get.assert_not_called()

    def test_late_flush_after_old_three_second_deadline_is_recognized(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        line = "CN\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Service is not available)"
        with patch('app.collector.gateway.fresh_journal_lines', side_effect=[[], [], [line]]), \
                patch('app.collector.gateway.time.monotonic', side_effect=[0, 1, 5]), \
                patch('app.collector.gateway.time.sleep'):
            self.assertEqual(login_failure(native, '.', {}, '12345', 'Example-Real'), 'NETWORK_UNAVAILABLE')

    def test_temporary_journal_read_error_is_retried_without_repeating_login(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        line = "CN\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Service is not available)"
        with patch('app.collector.gateway.fresh_journal_lines', side_effect=[PermissionError(), [line]]), \
                patch('app.collector.gateway.time.sleep') as sleep:
            self.assertEqual(login_failure(native, '.', {}, '12345', 'Example-Real'), 'NETWORK_UNAVAILABLE')
            sleep.assert_called_once()
            native.last_error.assert_called_once()
            native.login.assert_not_called()

    def test_only_fresh_matching_transport_pair_is_retryable(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private native text')))
        sync = "KD\t2\t14:46:34\tExample-Real\t'12345': error sending synchronization command\r\n"
        common = "OQ\t2\t14:46:34\tNetwork\t'12345': authorization on Example-Real failed (Common error)\r\n"
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'logs'; folder.mkdir()
            path = folder / '20261001.log'
            path.write_text(sync + common, encoding='utf-16')
            baseline = journal_baseline(root)
            classify = lambda login='12345', server='Example-Real': login_failure(native, root, baseline, login, server, settle_seconds=0)
            self.assertEqual(classify(), 'CONNECTION_FAILED')  # old evidence
            with path.open('ab') as out:
                out.write(common.encode('utf-16-le'))
            self.assertEqual(classify(), 'CONNECTION_FAILED')  # incomplete pair
            with path.open('ab') as out:
                out.write(sync.encode('utf-16-le'))
            self.assertEqual(classify(), 'NETWORK_UNAVAILABLE')
            self.assertEqual(classify(login='99999'), 'CONNECTION_FAILED')
            self.assertEqual(classify(server='Other'), 'CONNECTION_FAILED')
            self.assertEqual(login_failure(native, root, None, '12345', 'Example-Real'), 'CONNECTION_FAILED')

    def test_invalid_account_is_not_reclassified(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private native text')))
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'logs'; folder.mkdir()
            baseline = journal_baseline(root)
            path = folder / '20261001.log'
            path.write_text("KD\t2\ttime\tExample-Real\t'12345': error sending synchronization command\r\n"
                            "OQ\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Invalid account)\r\n", encoding='utf-16')
            self.assertEqual(login_failure(native, root, baseline, '12345', 'Example-Real', settle_seconds=0), 'AUTH_FAILED')

    def test_service_unavailable_requires_fresh_exact_account_server_source(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        line = "CN\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Service is not available)\r\n"
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'logs'; folder.mkdir()
            path = folder / '20261002.log'
            path.write_text(line, encoding='utf-16')
            baseline = journal_baseline(root)
            classify = lambda login='12345', server='Example-Real': login_failure(native, root, baseline, login, server, settle_seconds=0)
            self.assertEqual(classify(), 'CONNECTION_FAILED')
            with path.open('ab') as out:
                out.write(line.replace('Network', 'Other').encode('utf-16-le'))
            self.assertEqual(classify(), 'CONNECTION_FAILED')
            with path.open('ab') as out:
                out.write(line.encode('utf-16-le'))
            self.assertEqual(classify(), 'NETWORK_UNAVAILABLE')
            self.assertEqual(classify(login='54321'), 'CONNECTION_FAILED')
            self.assertEqual(classify(server='Other-Real'), 'CONNECTION_FAILED')

    def test_service_unavailable_never_overrides_explicit_rejection(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        unavailable = "CN\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Service is not available)"
        rejected = unavailable.replace('Service is not available', 'Invalid account')
        for lines in ([unavailable, rejected], [rejected, unavailable]):
            with self.subTest(lines=lines), patch('app.collector.gateway.fresh_journal_lines', return_value=lines):
                self.assertEqual(login_failure(native, '.', {}, '12345', 'Example-Real'), 'AUTH_FAILED')

    def test_rotated_evidence_is_ambiguous_and_only_allows_bounded_retry(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private native text')))
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'logs'; folder.mkdir()
            path = folder / '20261001.log'; path.write_text('old', encoding='utf-16')
            baseline = journal_baseline(root)
            path.write_bytes(b'')
            self.assertEqual(login_failure(native, root, baseline, '12345', 'Example-Real'), 'CONNECTION_FAILED')

    def test_delayed_flush_uses_original_baseline_and_error(self):
        native = Mock(last_error=Mock(side_effect=[(-6, 'private'), (1, 'success')]))
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'logs'; folder.mkdir()
            path = folder / '20261002.log'
            path.write_text('', encoding='utf-16')
            baseline = journal_baseline(root)
            def flush(_):
                with path.open('ab') as out:
                    out.write(("KD\t2\ttime\tExample-Real\t'12345': error sending synchronization command\r\n"
                               "OQ\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Common error)\r\n").encode('utf-16-le'))
            with patch('app.collector.gateway.time.sleep', side_effect=flush) as sleep:
                self.assertEqual(login_failure(native, root, baseline, '12345', 'Example-Real'), 'NETWORK_UNAVAILABLE')
            sleep.assert_called_once()
            native.last_error.assert_called_once()
            native.login.assert_not_called()
            native.history_deals_get.assert_not_called()

    def test_no_evidence_stops_at_deadline(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        with patch('app.collector.gateway.fresh_journal_lines', return_value=[]), \
                patch('app.collector.gateway.time.monotonic', side_effect=[10, 11, 20]), \
                patch('app.collector.gateway.time.sleep') as sleep:
            self.assertEqual(login_failure(native, '.', {}, '12345', 'Example-Real'), 'CONNECTION_FAILED')
        sleep.assert_called_once_with(0.1)
        native.last_error.assert_called_once()

    def test_explicit_rejection_overrides_matching_transport_pair(self):
        native = Mock(last_error=Mock(return_value=(-6, 'private')))
        lines = ["KD\t2\ttime\tExample-Real\t'12345': error sending synchronization command",
                 "OQ\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Common error)",
                 "OQ\t2\ttime\tNetwork\t'12345': authorization on Example-Real failed (Invalid account)"]
        with patch('app.collector.gateway.fresh_journal_lines', return_value=lines):
            self.assertEqual(login_failure(native, '.', {}, '12345', 'Example-Real'), 'AUTH_FAILED')
