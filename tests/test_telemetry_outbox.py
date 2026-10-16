from contextlib import closing
import json
import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch
from app.collector.telemetry_outbox import DiagnosticOutbox


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'diagnostics.sqlite3'
        self.client = Mock()
        self.box = DiagnosticOutbox(self.path, self.client, 'abcdef1', start=False)

    def emit(self, code='API_UNAVAILABLE', **tags):
        return self.box.capture_event({'tags': {'error_code': code, 'slot': 'demo-01', **tags},
                                       'password': 'SECRET', 'message': 'SECRET'})

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute('SELECT key,payload,attempts FROM diagnostic_outbox').fetchall()

    def due(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE diagnostic_outbox SET next_at=0')

    def test_failure_survives_restart_and_retries_same_event(self):
        event = self.emit()
        self.client.call.side_effect = OSError('SECRET')
        self.box.drain_one()
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(self.box.drain_one())
        restarted = DiagnosticOutbox(self.path, self.client, start=False)
        self.client.call.side_effect = None
        self.client.call.return_value = {'accepted': True, 'channel': 'sentry'}
        self.due()
        restarted.drain_one()
        self.assertEqual(self.client.call.call_args.args[1]['eventId'], event)
        self.assertEqual(self.rows(), [])
        self.assertIsNone(restarted.capture_event({'tags': {'error_code': 'API_UNAVAILABLE'}}))

    def test_negative_ack_is_not_delivery_even_with_http_success(self):
        self.emit()
        self.client.call.return_value = {'accepted': False, 'retryAfterSeconds': 300}
        self.box.drain_one()
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(self.box.drain_one())

    def test_quota_fallback_acknowledgement_completes_delivery(self):
        self.emit()
        self.client.call.return_value = {'accepted': True, 'channel': 'telegram'}
        self.box.drain_one()
        self.assertEqual(self.rows(), [])

    def test_queue_coalesces_across_instances_without_reserving_failed_sends(self):
        self.emit()
        other = DiagnosticOutbox(self.path, self.client, start=False)
        self.assertIsNone(other.capture_event({'tags': {'error_code': 'API_UNAVAILABLE', 'slot': 'demo-02'}}))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0][2], 0)

    def test_sensitive_fields_never_enter_persistent_queue(self):
        self.emit('API_HTTP_409', operation='complete', api_reason='RESULT_CONFLICT', native_error_code=-6, login='SECRET')
        encoded = self.rows()[0][1]
        self.assertNotIn('SECRET', encoded)
        data = json.loads(encoded)
        self.assertEqual(data['apiReason'], 'RESULT_CONFLICT')
        self.assertEqual(data['nativeErrorCode'], -6)
        self.assertEqual(data['release'], 'abcdef1')

    def test_retry_lease_excludes_other_process(self):
        self.emit()
        other = DiagnosticOutbox(self.path, self.client, start=False)
        def send(*args):
            self.assertFalse(other.drain_one())
            return {'accepted': True, 'channel': 'sentry'}
        self.client.call.side_effect = send
        self.box.drain_one()
        self.client.call.assert_called_once()

    def test_different_critical_conflicts_are_not_deduplicated(self):
        self.emit('API_HTTP_409', api_reason='RESULT_CONFLICT')
        self.emit('API_HTTP_409', api_reason='ACCOUNT_IDENTITY_MISMATCH')
        self.assertEqual(len(self.rows()), 2)
