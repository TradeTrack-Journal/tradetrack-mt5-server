"""Offline regression tests: no MT5 terminal, network or credentials required."""

from collections import namedtuple
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
import json
import sys
import unittest

from app.collector.contracts import EPOCH, UTC, DataError, HistoryWindow
from app.collector.normalization import normalize_deals, normalize_positions


def deal(**changes):
    row = dict(
        ticket=1, order=10, position_id=90, time_msc=1000, type=0, entry=0,
        magic=0, reason=0, symbol="EURUSD.pro", volume=1.0, price=1.12567,
        profit=0.0, commission=-2.0, swap=0.0, fee=-0.1,
    )
    return row | changes


def position(**changes):
    row = dict(
        ticket=91, identifier=90, time_msc=1000, time_update_msc=2000,
        type=0, magic=0, reason=0, symbol="EURUSD.pro", volume=1.0,
        price_open=1.12567, price_current=1.13, sl=0.0, tp=0.0,
        profit=5.0, swap=-0.5,
    )
    return row | changes


class WindowTests(unittest.TestCase):
    def test_timezone_offsets_describe_the_same_utc_interval(self):
        window = HistoryWindow(
            datetime.fromisoformat("2026-09-10T03:00:00.125+03:00"),
            datetime.fromisoformat("2026-09-10T03:00:01.126+03:00"),
        )
        self.assertEqual(window.start.hour, 0)
        self.assertEqual(window.start.tzinfo, UTC)
        self.assertEqual(window.end_ms - window.start_ms, 1001)
        self.assertEqual(window.start_ms % 1000, 125)
        lower, upper = window.mt5_bounds()
        self.assertEqual(lower, window.start.replace(microsecond=0))
        self.assertEqual(upper, window.end.replace(microsecond=0) + timedelta(seconds=1))

    def test_dst_fallback_is_measured_in_elapsed_time(self):
        window = HistoryWindow(
            datetime.fromisoformat("2026-10-25T02:30:00+02:00"),
            datetime.fromisoformat("2026-10-25T02:30:00+01:00"),
        )
        self.assertEqual(window.end_ms - window.start_ms, 3_600_000)

    def test_invalid_or_unbounded_windows_are_rejected(self):
        for start, end in (
            (datetime(2026, 1, 1), EPOCH),
            (EPOCH, EPOCH),
            (EPOCH + timedelta(seconds=1), EPOCH),
            (EPOCH, EPOCH + timedelta(days=31, milliseconds=1)),
            (EPOCH - timedelta(seconds=1), EPOCH),
            (EPOCH, EPOCH + timedelta(microseconds=1)),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(DataError):
                HistoryWindow(start, end)

    def test_maximum_window_and_exact_second_bounds(self):
        window = HistoryWindow(EPOCH, EPOCH + timedelta(days=31))
        self.assertEqual(window.mt5_bounds(), (window.start, window.end))

    def test_datetime_overflow_is_a_controlled_window_error(self):
        with self.assertRaises(DataError):
            HistoryWindow(EPOCH, datetime.fromisoformat("9999-12-31T23:30:00-01:00"))
        window = HistoryWindow(
            datetime(9999, 12, 31, tzinfo=UTC),
            datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
        )
        with self.assertRaises(DataError):
            window.mt5_bounds()


class DealTests(unittest.TestCase):
    def setUp(self):
        self.window = HistoryWindow(EPOCH, EPOCH + timedelta(seconds=10))

    def test_partial_closes_keep_opening_and_all_costs(self):
        records = [
            deal(),
            deal(ticket=2, time_msc=2000, type=1, entry=1, volume=0.4,
                 profit=40.0, commission=-0.8),
            deal(ticket=3, time_msc=3000, type=1, entry=1, volume=0.6,
                 profit=60.0, commission=-1.2, swap=-0.5),
        ]
        output = normalize_deals(records, self.window)
        self.assertEqual(len(output), 3)
        self.assertEqual(sum(Decimal(d.profit) for d in output), Decimal("100"))
        self.assertEqual(sum(Decimal(d.commission) for d in output), Decimal("-4"))
        self.assertEqual(sum(Decimal(d.fee) for d in output), Decimal("-0.3"))
        self.assertEqual(sum(Decimal(d.swap) for d in output), Decimal("-0.5"))
        self.assertEqual([(d.type, d.entry) for d in output], [(0, 0), (1, 1), (1, 1)])
        self.assertNotIn("direction", asdict(output[0]))

    def test_reversal_close_by_cashflow_and_unknown_types_are_preserved(self):
        records = [
            deal(ticket=1, entry=2),
            deal(ticket=2, entry=3),
            deal(ticket=3, type=2, position_id=0, order=0, symbol="", volume=0),
            deal(ticket=4, type=987, entry=654),
        ]
        output = normalize_deals(records, self.window)
        self.assertEqual([(d.type, d.entry) for d in output], [(0, 2), (0, 3), (2, 0), (987, 654)])
        self.assertEqual(output[2].position_id, "0")

    def test_missing_opening_is_not_replaced_by_fabricated_open_time(self):
        output = normalize_deals([deal(type=1, entry=1)], self.window)
        self.assertEqual(output[0].entry, 1)
        self.assertNotIn("time_open", asdict(output[0]))

    def test_unsigned_64_bit_ids_survive_json(self):
        largest = 2**64 - 1
        record = deal(ticket=largest, order=largest - 1, position_id=largest - 2)
        output = json.loads(json.dumps(asdict(normalize_deals([record], self.window)[0])))
        self.assertEqual(output["ticket"], str(largest))
        self.assertEqual(output["order"], str(largest - 1))
        self.assertEqual(output["position_id"], str(largest - 2))
        self.assertEqual(output["price"], "1.12567")

    def test_native_namedtuples_are_supported_without_exporting_private_extras(self):
        row = deal(comment="private note", external_id="private external ID")
        native = namedtuple("TradeDeal", row.keys())(**row)
        output = json.dumps(asdict(normalize_deals((native,), self.window)[0]))
        self.assertNotIn("private", output)
        self.assertNotIn("external_id", output)

    def test_half_open_windows_and_numeric_tie_sorting(self):
        window = HistoryWindow(EPOCH + timedelta(milliseconds=1001), EPOCH + timedelta(milliseconds=2001))
        records = [deal(ticket=20, time_msc=1001), deal(ticket=3, time_msc=1001),
                   deal(ticket=4, time_msc=2000), deal(ticket=5, time_msc=2001),
                   deal(ticket=6, time_msc=1000)]
        self.assertEqual([d.ticket for d in normalize_deals(records, window)], ["3", "20", "4"])

    def test_identical_duplicates_are_idempotent_but_conflicts_fail(self):
        self.assertEqual(len(normalize_deals([deal(), deal()], self.window)), 1)
        with self.assertRaises(DataError) as error:
            normalize_deals([deal(), deal(profit=12)], self.window)
        self.assertEqual(error.exception.code, "CONFLICTING_DEAL")

    def test_one_malformed_record_fails_batch_without_secret_in_error(self):
        for field in ("fee", "time_msc", "position_id", "symbol"):
            row = deal(ticket=2)
            row[field] = {"password": "test-secret-do-not-log"}
            with self.subTest(field=field), self.assertRaises(DataError) as error:
                normalize_deals([deal(), row], self.window)
            self.assertNotIn("test-secret", str(error.exception))
            self.assertEqual(error.exception.code, "INVALID_RECORD")

    def test_missing_costs_are_not_silently_zeroed(self):
        for field in ("fee", "commission", "swap", "profit"):
            row = deal()
            del row[field]
            with self.subTest(field=field), self.assertRaises(DataError):
                normalize_deals([row], self.window)

    def test_invalid_ids_are_not_coerced(self):
        for value in (True, 1.0, "123", -1, 0, 2**64):
            with self.subTest(value=value), self.assertRaises(DataError):
                normalize_deals([deal(ticket=value)], self.window)

    def test_nonfinite_money_and_negative_volume_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), Decimal("sNaN"), True):
            with self.subTest(value=value), self.assertRaises(DataError):
                normalize_deals([deal(profit=value)], self.window)
        with self.assertRaises(DataError):
            normalize_deals([deal(volume=-0.1)], self.window)
        # Negative prices/P&L and broker rebates must not be clamped.
        output = normalize_deals([deal(price=-10, profit=-20, commission=2)], self.window)
        self.assertEqual((output[0].price, output[0].profit, output[0].commission), ("-10", "-20", "2"))

    def test_none_is_error_but_empty_tuple_is_valid(self):
        self.assertEqual(normalize_deals((), self.window), ())
        with self.assertRaises(DataError) as error:
            normalize_deals(None, self.window)
        self.assertEqual(error.exception.code, "HISTORY_UNAVAILABLE")

    def test_limits_apply_before_filtering_and_deduplication(self):
        for records in ([deal(), deal()], [deal(time_msc=999999), deal(time_msc=999998)]):
            with self.subTest(records=records), self.assertRaises(DataError) as error:
                normalize_deals(records, self.window, max_records=1)
            self.assertEqual(error.exception.code, "BATCH_TOO_LARGE")

    def test_unbounded_iterators_and_invalid_limits_are_rejected(self):
        with self.assertRaises(DataError):
            normalize_deals(iter([deal()]), self.window)
        for limit in (0, -1, True, 50_001, "10"):
            with self.subTest(limit=limit), self.assertRaises(DataError):
                normalize_deals([], self.window, max_records=limit)


class PositionTests(unittest.TestCase):
    def test_position_identity_is_distinct_from_ticket_and_stays_precise(self):
        output = normalize_positions([position(ticket=2**63, identifier=2**63 + 1)])
        wire = json.loads(json.dumps(asdict(output[0])))
        self.assertEqual(wire["ticket"], str(2**63))
        self.assertEqual(wire["identifier"], str(2**63 + 1))
        self.assertEqual(wire["price_open"], "1.12567")
        self.assertEqual(wire["swap"], "-0.5")

    def test_failed_snapshot_cannot_be_mistaken_for_flat_account(self):
        self.assertEqual(normalize_positions(()), ())
        with self.assertRaises(DataError) as error:
            normalize_positions(None)
        self.assertEqual(error.exception.code, "POSITIONS_UNAVAILABLE")
        with self.assertRaises(DataError):
            normalize_positions([position(), position(ticket=92, identifier=92, profit=None)])

    def test_conflicting_ticket_or_identifier_cannot_replace_position(self):
        self.assertEqual(len(normalize_positions([position(), position()])), 1)
        for second in (position(profit=9), position(ticket=92), position(identifier=92)):
            with self.subTest(second=second), self.assertRaises(DataError) as error:
                normalize_positions([position(), second])
            self.assertEqual(error.exception.code, "CONFLICTING_POSITION")

    def test_snapshot_limits_and_missing_fields(self):
        with self.assertRaises(DataError):
            normalize_positions([position(), position()], max_records=1)
        row = position()
        del row["time_update_msc"]
        with self.assertRaises(DataError):
            normalize_positions([row])

    def test_importing_and_testing_core_does_not_load_native_mt5(self):
        self.assertNotIn("MetaTrader5", sys.modules)


if __name__ == "__main__":
    unittest.main()
