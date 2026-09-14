"""Immutable wire records for the collector; no trade aggregation here."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_WINDOW = timedelta(days=31)
MAX_RECORDS = 50_000
SCHEMA_VERSION = 1


class DataError(ValueError):
    """Safe error: codes/field names only, never native values or credentials."""

    def __init__(self, code: str, field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(f"{code}: {field}" if field else code)


def _epoch_ms(value: datetime) -> int:
    delta = value - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


@dataclass(frozen=True)
class HistoryWindow:
    """Half-open numeric MT5 interval with aligned, bounded endpoints.

    Endpoints use UTC-aware datetime encoding to avoid host-timezone conversion.
    This does not establish that broker-reported time_msc values denote UTC.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for field in ("start", "end"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise DataError("INVALID_WINDOW", field)
            try:
                value = value.astimezone(UTC)
            except (OverflowError, ValueError):
                raise DataError("INVALID_WINDOW", field) from None
            if value < EPOCH or value.microsecond % 1000:
                raise DataError("INVALID_WINDOW", field)
            object.__setattr__(self, field, value)
        if not timedelta(0) < self.end - self.start <= MAX_WINDOW:
            raise DataError("INVALID_WINDOW", "duration")

    @property
    def start_ms(self) -> int:
        return _epoch_ms(self.start)

    @property
    def end_ms(self) -> int:
        return _epoch_ms(self.end)

    def mt5_bounds(self) -> tuple[datetime, datetime]:
        """Expand to whole seconds; filter the response using the exact window."""
        start = self.start.replace(microsecond=0)
        end = self.end.replace(microsecond=0)
        if self.end.microsecond:
            try:
                end += timedelta(seconds=1)
            except OverflowError:
                raise DataError("INVALID_WINDOW", "end") from None
        return start, end


@dataclass(frozen=True)
class RawDeal:
    # Identifiers and financial values deliberately cross JSON as strings.
    ticket: str
    order: str
    position_id: str
    time_msc: int  # Preserved broker value; UTC interpretation is not implied.
    type: int
    entry: int
    magic: str
    reason: int
    symbol: str
    volume: str
    price: str
    profit: str
    commission: str
    swap: str
    fee: str


@dataclass(frozen=True)
class RawPosition:
    ticket: str
    identifier: str
    time_msc: int  # Preserved broker value; UTC interpretation is not implied.
    time_update_msc: int
    type: int
    magic: str
    reason: int
    symbol: str
    volume: str
    price_open: str
    price_current: str
    sl: str
    tp: str
    profit: str
    swap: str


@dataclass(frozen=True)
class RawAccountSnapshot:
    balance: str
    equity: str
    currency: str
