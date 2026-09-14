"""Validate selected native MT5 fields without silently dropping financial data.

The caller must enforce session identity, investor authorization, deadlines and
snapshot completeness. These pure functions cannot establish any of those facts.
Serializing dataclasses.asdict(record) produces the version 1 wire representation.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
import unicodedata

from .contracts import MAX_RECORDS, DataError, HistoryWindow, RawDeal, RawPosition, RawAccountSnapshot


UINT64_MAX = 2**64 - 1
INT64_MAX = 2**63 - 1


def _record(value: Any) -> Mapping[str, Any]:
    if isinstance(value, tuple) and callable(getattr(value, "_asdict", None)):
        value = value._asdict()
    if not isinstance(value, Mapping):
        raise DataError("INVALID_RECORD")
    return value


def _int(row: Mapping[str, Any], field: str, maximum: int = INT64_MAX) -> int:
    value = row.get(field)
    if type(value) is not int or not 0 <= value <= maximum:
        raise DataError("INVALID_RECORD", field)
    return value


def _id(row: Mapping[str, Any], field: str, *, nonzero: bool = False) -> str:
    value = _int(row, field, UINT64_MAX)
    if nonzero and value == 0:
        raise DataError("INVALID_RECORD", field)
    return str(value)


def _number(row: Mapping[str, Any], field: str, *, nonnegative: bool = False) -> str:
    value = row.get(field)
    if type(value) not in (int, float, Decimal):
        raise DataError("INVALID_RECORD", field)
    # str(float) retains its shortest round-trip decimal, without inventing
    # precision or rounding fees/prices to the account currency's minor unit.
    decimal = Decimal(str(value))
    if not decimal.is_finite() or (nonnegative and decimal < 0):
        raise DataError("INVALID_RECORD", field)
    return str(decimal)


def _symbol(row: Mapping[str, Any]) -> str:
    value = row.get("symbol")
    # Cash operations can have no symbol. Preserve spelling and suffixes.
    if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise DataError("INVALID_RECORD", "symbol")
    return value


def _batch(records: Any, unavailable_code: str, max_records: int) -> tuple | list:
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise DataError("INVALID_LIMIT")
    if records is None:
        raise DataError(unavailable_code)
    # Native MT5 returns a tuple. No unbounded iterator materialization here.
    if not isinstance(records, (tuple, list)):
        raise DataError("INVALID_BATCH")
    if len(records) > max_records:
        raise DataError("BATCH_TOO_LARGE")
    return records


def normalize_account(value: Any) -> RawAccountSnapshot:
    row = _record(value)
    currency = row.get("currency")
    if (not isinstance(currency, str) or not 1 <= len(currency) <= 10 or currency != currency.strip() or
            any(unicodedata.category(char) in ("Cc", "Cf") for char in currency)):
        raise DataError("INVALID_RECORD", "currency")
    # Zero and negative balances are valid; never substitute them with initial nominal.
    return RawAccountSnapshot(balance=_number(row, "balance"), equity=_number(row, "equity"), currency=currency)


def normalize_deals(
    records: Any, window: HistoryWindow, *, max_records: int = MAX_RECORDS
) -> tuple[RawDeal, ...]:
    """Keep every unique deal in [start,end), including non-trading operations.

    Limit applies to the native response before filtering. Conflicting tickets
    in one response fail the whole batch; later syncs may legitimately correct a
    previously stored ticket and must be upserted by the backend.
    """
    start_ms, end_ms = window.start_ms, window.end_ms
    result: dict[str, RawDeal] = {}
    for value in _batch(records, "HISTORY_UNAVAILABLE", max_records):
        row = _record(value)
        time_msc = _int(row, "time_msc")
        if not start_ms <= time_msc < end_ms:
            continue
        deal = RawDeal(
            ticket=_id(row, "ticket", nonzero=True),
            order=_id(row, "order"),
            position_id=_id(row, "position_id"),
            time_msc=time_msc,
            type=_int(row, "type"),
            entry=_int(row, "entry"),
            magic=_id(row, "magic"),
            reason=_int(row, "reason"),
            symbol=_symbol(row),
            volume=_number(row, "volume", nonnegative=True),
            price=_number(row, "price"),
            profit=_number(row, "profit"),
            commission=_number(row, "commission"),
            swap=_number(row, "swap"),
            fee=_number(row, "fee"),
        )
        if deal.ticket in result and result[deal.ticket] != deal:
            raise DataError("CONFLICTING_DEAL")
        result[deal.ticket] = deal
    return tuple(sorted(result.values(), key=lambda deal: (deal.time_msc, int(deal.ticket))))


def normalize_positions(
    records: Any, *, max_records: int = MAX_RECORDS
) -> tuple[RawPosition, ...]:
    """Normalize one positions_get response; None never means a flat account."""
    result: dict[str, RawPosition] = {}
    tickets: set[str] = set()
    for value in _batch(records, "POSITIONS_UNAVAILABLE", max_records):
        row = _record(value)
        position = RawPosition(
            ticket=_id(row, "ticket", nonzero=True),
            identifier=_id(row, "identifier", nonzero=True),
            time_msc=_int(row, "time_msc"),
            time_update_msc=_int(row, "time_update_msc"),
            type=_int(row, "type"),
            magic=_id(row, "magic"),
            reason=_int(row, "reason"),
            symbol=_symbol(row),
            volume=_number(row, "volume", nonnegative=True),
            price_open=_number(row, "price_open"),
            price_current=_number(row, "price_current"),
            sl=_number(row, "sl"),
            tp=_number(row, "tp"),
            profit=_number(row, "profit"),
            swap=_number(row, "swap"),
        )
        previous = result.get(position.identifier)
        if previous is not None:
            if previous != position:
                raise DataError("CONFLICTING_POSITION")
            continue
        if position.ticket in tickets:
            raise DataError("CONFLICTING_POSITION")
        result[position.identifier] = position
        tickets.add(position.ticket)
    return tuple(sorted(result.values(), key=lambda position: int(position.identifier)))
