import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from app.collector import telemetry


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('_seen', {}), ('_pending', {}), ('_state_path', None)]:
            patcher = patch.object(telemetry, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_short_restarts_and_account_action_errors_stay_in_logs(self):
        sdk = Mock()
        with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry.time, 'monotonic') as clock:
            for now, state in [(0, 'TERMINAL_CHANGED'), (30, 'TERMINAL_CHANGED'), (60, None),
                               (190, 'TERMINAL_CHANGED'), (240, 'TERMINAL_CHANGED')]:
                clock.return_value = now
                telemetry.report({'slotId': 'demo-10', 'errorCode': state, 'state': 'idle' if state is None else 'retrying'})
            for code in telemetry.LOCAL_ONLY:
                telemetry.report({'slotId': 'demo-10', 'errorCode': code})
            sdk.capture_event.assert_not_called()

    def test_sustained_transient_escalates_but_not_once_per_slot(self):
        sdk = Mock()
        with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry.time, 'monotonic') as clock:
            for now in [0, 60, 180, 900, 1800]:
                clock.return_value = now
                for slot in ['demo-01', 'demo-10']:
                    telemetry.report({'slotId': slot, 'errorCode': 'TERMINAL_CHANGED'})
                if now < 180:
                    sdk.capture_event.assert_not_called()
            self.assertEqual(sdk.capture_event.call_count, 1)

    def test_separated_transient_failures_do_not_accumulate_into_false_incident(self):
        sdk = Mock()
        with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry.time, 'monotonic') as clock:
            for now in [0, 1000, 2000, 3000]:
                clock.return_value = now
                telemetry.report({'slotId': 'demo-02', 'errorCode': 'UI_DIALOG_UNAVAILABLE'})
            sdk.capture_event.assert_not_called()

    def test_critical_fault_is_immediate_and_quota_survives_process_state_reset(self):
        with TemporaryDirectory() as folder:
            sdk = Mock()
            with patch.object(telemetry, '_sdk', sdk), \
                    patch.object(telemetry, '_state_path', Path(folder) / 'telemetry.sqlite3'), \
                    patch.object(telemetry.time, 'time', return_value=100000) as clock:
                telemetry.report({'slotId': 'demo-10', 'errorCode': 'IDENTITY_DRIFT'})
                self.assertEqual(sdk.capture_event.call_count, 1)
                telemetry._seen.clear()
                telemetry._pending.clear()
                telemetry.report({'slotId': 'demo-01', 'errorCode': 'IDENTITY_DRIFT'})
                self.assertEqual(sdk.capture_event.call_count, 1)
                telemetry.report({'errorCode': 'WORKER_START_FAILED'})
                self.assertEqual(sdk.capture_event.call_count, 2)
                clock.return_value += telemetry.REMINDER_SECONDS + 1
                telemetry.report({'slotId': 'demo-01', 'errorCode': 'IDENTITY_DRIFT'})
                self.assertEqual(sdk.capture_event.call_count, 3)

    def test_broken_persistent_store_falls_back_to_bounded_memory_quota(self):
        with TemporaryDirectory() as folder:
            sdk = Mock()
            with patch.object(telemetry, '_sdk', sdk), patch.object(telemetry, '_state_path', Path(folder)):
                for _ in range(10):
                    telemetry.report({'errorCode': 'WORKER_FAILED'})
                self.assertEqual(sdk.capture_event.call_count, 1)

    def test_all_managed_slots_are_allowlisted(self):
        for slot in ['demo-01', 'demo-06', 'demo-10', 'demo-16']:
            event = telemetry.sanitize({'tags': {'error_code': 'WORKER_FAILED', 'slot': slot}}, {})
            self.assertEqual(event['tags']['slot'], slot)
        self.assertEqual(telemetry.safe_slot('SECRET'), 'controller')

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
