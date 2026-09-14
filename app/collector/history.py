"""Bounded recent-history sampling when the broker's time basis is unresolved.

MT5 deal time_msc must be preserved as broker-reported data. Encoding query
endpoints as aware UTC datetimes makes Python conversion deterministic; it does
not prove that native deal timestamps represent UTC instants.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .contracts import UTC, EPOCH, DataError, HistoryWindow


@dataclass(frozen=True)
class RecentHistoryPlan:
    observed_at_utc: datetime
    native_window: HistoryWindow
    timestamp_basis: Literal["broker_reported_unresolved"] = "broker_reported_unresolved"
    utc_cursor_advance_allowed: Literal[False] = False


def plan_recent_history(observed_at: datetime) -> RecentHistoryPlan:
    """Request a 31-day native envelope around a 29-day recent UTC interval.

    One day on each side is a bounded diagnostic margin, not a detected broker
    offset and not proof of completeness for every server. Never use the padded
    end as a successful UTC sync cursor. Historical conversion requires verified
    per-server timezone/DST metadata; do not subtract today's offset from history.
    """
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise DataError("INVALID_OBSERVATION_TIME")
    try:
        observed = observed_at.astimezone(UTC).replace(microsecond=0)
        window = HistoryWindow(observed - timedelta(days=30), observed + timedelta(days=1))
    except (ValueError, OverflowError):
        raise DataError("INVALID_OBSERVATION_TIME") from None
    return RecentHistoryPlan(observed, window)


def plan_history_before(before_ms):
    if type(before_ms) is not int or not 0 < before_ms < 4102444800000:
        raise DataError("INVALID_WINDOW")
    end = EPOCH + timedelta(milliseconds=before_ms)
    return HistoryWindow(max(EPOCH, end - timedelta(days=31)), end)
