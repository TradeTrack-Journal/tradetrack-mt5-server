from datetime import datetime, timedelta, timezone
import unittest

from scripts.mt5_maintenance_preflight import heartbeat_fresh, summarize


class MaintenancePreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 11, tzinfo=timezone.utc)
        self.config = {'slots': [{'id': 'demo-01'}]}
        self.remote = {'demo-01': {'quarantineIdentity': None}}
        self.slot = {'id': 'demo-01', 'status': 'STARTING',
                     'lastHeartbeat': self.now.isoformat(), 'errorCode': None,
                     'privateUnexpectedField': 'must-not-be-output'}

    def report(self, slots=None, builds=None):
        return summarize(self.config, self.remote, {'slots': slots if slots is not None else [self.slot]},
                         builds if builds is not None else {'demo-01': 6231}, {6231}, self.now)

    def test_healthy_report_never_authorizes_update_or_leaks_extra_fields(self):
        report = self.report()
        self.assertEqual(report['healthySlots'], 1)
        self.assertFalse(report['automaticInstallationEnabled'])
        self.assertNotIn('must-not-be-output', str(report))

    def test_missing_duplicate_or_unexpected_slot_is_not_healthy(self):
        for slots in ([], [self.slot, self.slot], [dict(self.slot, id='other')]):
            self.assertEqual(self.report(slots)['healthySlots'], 0)

    def test_stale_future_naive_or_invalid_heartbeat_is_not_healthy(self):
        for value in (None, 'bad', '2026-10-14T11:00:00',
                      (self.now - timedelta(seconds=121)).isoformat(),
                      (self.now + timedelta(seconds=6)).isoformat()):
            self.assertFalse(heartbeat_fresh(value, self.now))

    def test_fence_error_offline_and_unreviewed_build_are_not_healthy(self):
        for fields in ({'errorCode': 'LEASE_EXPIRED'}, {'status': 'OFFLINE'}):
            self.assertEqual(self.report([dict(self.slot, **fields)])['healthySlots'], 0)
        for build in (None, 6259, 6246):  # 6246 locally reviewed but not API-approved in fixture.
            self.assertEqual(self.report(builds={'demo-01': build})['healthySlots'], 0)
        self.remote['demo-01']['quarantineIdentity'] = 'old-process'
        self.assertEqual(self.report()['healthySlots'], 0)


if __name__ == '__main__':
    unittest.main()
