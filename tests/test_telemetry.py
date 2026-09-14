import unittest
from unittest.mock import Mock, patch
from app.collector import telemetry


class TelemetryTests(unittest.TestCase):
    def test_payload_is_allowlisted(self):
        event = telemetry.sanitize({'tags': {'error_code': 'API_UNAVAILABLE', 'slot': 'demo-01', 'login': 'SECRET'},
                                    'request': {'password': 'SECRET'}, 'extra': {'trades': 'SECRET'},
                                    'breadcrumbs': ['SECRET'], 'user': {'email': 'SECRET'}}, {})
        self.assertNotIn('SECRET', str(event))
        self.assertEqual(event['tags']['slot'], 'demo-01')

    def test_repeats_and_untrusted_fields(self):
        sdk = Mock()
        with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry, '_seen', {}):
            telemetry.report({'errorCode': 'API_UNAVAILABLE', 'slotId': 'demo-01', 'password': 'SECRET'})
            telemetry.report({'errorCode': 'API_UNAVAILABLE', 'slotId': 'demo-01'})
            self.assertEqual(sdk.capture_event.call_count, 1)
            self.assertNotIn('SECRET', str(sdk.capture_event.call_args))

    def test_transport_failure_does_not_fail_worker(self):
        sdk = Mock()
        sdk.capture_event.side_effect = RuntimeError('network unavailable')
        with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry, '_seen', {}):
            self.assertIsNone(telemetry.report({'errorCode': 'WORKER_FAILED'}))
