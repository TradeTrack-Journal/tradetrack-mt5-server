from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from app.collector.contracts import UTC, DataError, HistoryWindow
from app.collector.history import plan_recent_history
from app.collector.normalization import normalize_deals
from test_collector_data import deal


class HistoryClockTests(unittest.TestCase):
    def test_broker_wall_clock_ahead_does_not_hide_already_closed_deals(self):
        # Anonymized regression: native time labels 17:33 vs observation 15:06 UTC.
        observed = datetime(2026, 9, 10, 15, 6, 31, tzinfo=UTC)
        closed_ms = HistoryWindow(
            datetime(2026, 9, 10, 17, 33, 3, tzinfo=UTC),
            datetime(2026, 9, 10, 17, 34, 0, tzinfo=UTC),
        ).start_ms
        records = [deal(ticket=101, entry=1, time_msc=closed_ms, profit=42.87),
                   deal(ticket=102, entry=1, time_msc=closed_ms + 19000, profit=41.58)]
        old_window = HistoryWindow(observed - timedelta(days=30), observed)
        self.assertEqual(normalize_deals(records, old_window), ())
        plan = plan_recent_history(observed)
        result = normalize_deals(records, plan.native_window)
        self.assertEqual(len(result), 2)
        self.assertEqual(sum(Decimal(d.profit) for d in result), Decimal("84.45"))
        self.assertEqual(result[0].time_msc, closed_ms)
        self.assertEqual(plan.timestamp_basis, "broker_reported_unresolved")
        self.assertFalse(plan.utc_cursor_advance_allowed)

    def test_margin_includes_negative_server_offset_at_recent_lower_boundary(self):
        observed = datetime(2026, 9, 10, 15, tzinfo=UTC)
        plan = plan_recent_history(observed)
        near_start = observed - timedelta(days=29, hours=12)
        native_ms = HistoryWindow(near_start, near_start + timedelta(seconds=1)).start_ms
        self.assertEqual(len(normalize_deals([deal(time_msc=native_ms)], plan.native_window)), 1)
        self.assertEqual(plan.native_window.end - plan.native_window.start, timedelta(days=31))

    def test_observation_timezone_does_not_change_native_query(self):
        local = datetime.fromisoformat("2026-09-10T17:06:31.123+02:00")
        utc = datetime.fromisoformat("2026-09-10T15:06:31.123+00:00")
        self.assertEqual(plan_recent_history(local), plan_recent_history(utc))

    def test_invalid_observation_is_rejected(self):
        for value in (None, datetime(2026, 1, 1), datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC)):
            with self.subTest(value=value), self.assertRaises(DataError):
                plan_recent_history(value)


if __name__ == "__main__":
    unittest.main()
