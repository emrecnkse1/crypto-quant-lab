"""Read-only adapter for Binance's own basis series (FUNDING_RESEARCH_SPEC.md Bölüm 15.2, 15.6).

`GET https://fapi.binance.com/futures/data/basis` — public, no API key.
Role: a bounded, recent-period cross-check of the locally derived close basis
(research/basis.py) — NOT a long-history source and NOT an oracle.

Official contract (developers.binance.com, accessed 2026-09-23):
- parameters `pair`, `contractType` in {PERPETUAL, CURRENT_QUARTER,
  NEXT_QUARTER}, `period` in {5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d},
  optional `limit` (default 30, max 500), `startTime`, `endTime`;
- "Only the data of the latest 30 days is available.";
- response fields indexPrice, contractType, basisRate, futuresPrice,
  annualizedBasisRate, basis, pair, timestamp — with NO formula for `basis`
  or `basisRate` on the page.
Live responses (2026-09-23) show `annualizedBasisRate` = "" for PERPETUAL,
`basisRate` with 4 decimals, an inclusive `endTime`, and — for 1h/PERPETUAL —
`futuresPrice`/`indexPrice` equal to the contract-trade / index-price kline
OPEN at `timestamp`: the record is a snapshot at `timestamp`, not an
aggregate of the period that starts there. Nothing here depends on that
observation; the comparison layer documents it.

A request older than the documented 30-day window is refused (fail-closed)
instead of being silently truncated. The clock is always an explicit
`as_of_time`; nothing reads the wall clock. One request, no pagination: a
range needing more than `limit` records is refused.
"""

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)

from crypto_quant_lab.market_data.binance_usdm import USDM_BASE_URL, fetch_usdm_json_list
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

OFFICIAL_BASIS_PATH = "/futures/data/basis"
OFFICIAL_BASIS_MAX_LIMIT = 500
OFFICIAL_BASIS_HISTORY_WINDOW = timedelta(days=30)
OFFICIAL_BASIS_CONTRACT_TYPES = ("PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER")
OFFICIAL_BASIS_PERIODS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
}
# basisRate is displayed with 4 decimals (doc example "0.0004", live responses);
# half a unit of that last place bounds the display rounding.
OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE = Decimal("0.00005")

_FIELDS = frozenset(
    {
        "indexPrice",
        "contractType",
        "basisRate",
        "futuresPrice",
        "annualizedBasisRate",
        "basis",
        "pair",
        "timestamp",
    }
)
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EXACT = Context(
    prec=100,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)
_RATE = Context(
    prec=34, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation, DivisionByZero, Overflow]
)


@dataclass(frozen=True, slots=True)
class BinanceOfficialBasisRecord:
    """One `/futures/data/basis` row, verbatim as Decimals (nothing derived)."""

    pair: str
    contract_type: str
    period: str
    timestamp: datetime
    futures_price: Decimal
    index_price: Decimal
    basis: Decimal
    basis_rate: Decimal
    annualized_basis_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class OfficialBasisConsistency:
    """Algebraic check of one official record against its own price fields.

    `basis_residual = basis - (futuresPrice - indexPrice)` (exact);
    `basis_rate_residual = basisRate - basis / indexPrice` (34 significant
    digits). Consistent iff the basis residual is exactly 0 and the rate
    residual is within `OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE` (inclusive).
    """

    basis_residual: Decimal
    basis_rate_residual: Decimal
    is_consistent: bool


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string, got {type(value).__name__}")  # noqa: TRY004
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return parsed


def _validate_request(pair: str, contract_type: str, period: str) -> None:
    if not isinstance(pair, str) or not pair:
        raise ValueError("pair cannot be empty")
    if contract_type not in OFFICIAL_BASIS_CONTRACT_TYPES:
        raise ValueError(
            f"contract_type must be one of {OFFICIAL_BASIS_CONTRACT_TYPES}, got {contract_type!r}"
        )
    if period not in OFFICIAL_BASIS_PERIODS:
        raise ValueError(f"period must be one of {tuple(OFFICIAL_BASIS_PERIODS)}, got {period!r}")


def parse_binance_official_basis_record(
    raw: object, *, pair: str, contract_type: str, period: str
) -> BinanceOfficialBasisRecord:
    """Strictly parse one row; unknown/missing keys, mismatches and bad values fail."""
    _validate_request(pair, contract_type, period)
    if not isinstance(raw, dict):
        raise ValueError(f"basis row must be a JSON object, got {type(raw).__name__}")  # noqa: TRY004
    if set(raw) != _FIELDS:
        raise ValueError(f"basis row keys {sorted(raw)!r} != expected {sorted(_FIELDS)!r}")
    if raw["pair"] != pair or raw["contractType"] != contract_type:
        raise ValueError(
            f"basis row ({raw['pair']!r}, {raw['contractType']!r}) does not match "
            f"requested ({pair!r}, {contract_type!r})"
        )
    timestamp_ms = raw["timestamp"]
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise ValueError(f"invalid timestamp: {timestamp_ms!r}")
    period_ms = OFFICIAL_BASIS_PERIODS[period] // timedelta(milliseconds=1)
    if timestamp_ms % period_ms:
        raise ValueError(f"timestamp {timestamp_ms} is not aligned to the {period!r} grid")
    futures_price = _decimal(raw["futuresPrice"], "futuresPrice")
    index_price = _decimal(raw["indexPrice"], "indexPrice")
    if futures_price <= 0 or index_price <= 0:
        raise ValueError(f"prices must be > 0, got futures={futures_price}, index={index_price}")
    annualized = raw["annualizedBasisRate"]
    return BinanceOfficialBasisRecord(
        pair=pair,
        contract_type=contract_type,
        period=period,
        timestamp=_UTC_EPOCH + timedelta(milliseconds=timestamp_ms),
        futures_price=futures_price,
        index_price=index_price,
        basis=_decimal(raw["basis"], "basis"),
        basis_rate=_decimal(raw["basisRate"], "basisRate"),
        annualized_basis_rate=None
        if annualized == ""
        else _decimal(annualized, "annualizedBasisRate"),
    )


def build_official_basis_url(
    pair: str, contract_type: str, period: str, *, start_time_ms: int, end_time_ms: int, limit: int
) -> str:
    _validate_request(pair, contract_type, period)
    for name, value in (("start_time_ms", start_time_ms), ("end_time_ms", end_time_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    if start_time_ms > end_time_ms:
        raise ValueError(f"start_time_ms ({start_time_ms}) must be <= end_time_ms ({end_time_ms})")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= OFFICIAL_BASIS_MAX_LIMIT
    ):
        raise ValueError(
            f"limit must be an int between 1 and {OFFICIAL_BASIS_MAX_LIMIT}, got {limit!r}"
        )
    query = urllib.parse.urlencode(
        {
            "pair": pair,
            "contractType": contract_type,
            "period": period,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": limit,
        }
    )
    return f"{USDM_BASE_URL}{OFFICIAL_BASIS_PATH}?{query}"


def fetch_binance_official_basis(
    pair: str,
    contract_type: str,
    period: str,
    *,
    start_time: datetime,
    end_time: datetime,
    as_of_time: datetime,
    limit: int = OFFICIAL_BASIS_MAX_LIMIT,
    timeout: float = 10.0,
    fetch_rows: Callable[[str, int, float], list[object]] | None = None,
) -> tuple[BinanceOfficialBasisRecord, ...]:
    """Official records with `timestamp` in `[start_time, end_time)`, strictly ascending.

    Refuses (ValueError) a range starting before `as_of_time - 30 days`, ending
    after `as_of_time`, not aligned to the period grid, or needing more than
    `limit` records. Unordered/duplicate/foreign rows fail. `fetch_rows`
    (url, limit, timeout) -> list replaces the HTTP call in tests.
    """
    _validate_request(pair, contract_type, period)
    if timeout <= 0:
        raise ValueError(f"timeout must be greater than 0, got {timeout}")
    start_us = datetime_to_epoch_us(start_time)
    end_us = datetime_to_epoch_us(end_time)
    as_of_us = datetime_to_epoch_us(as_of_time)
    period_us = OFFICIAL_BASIS_PERIODS[period] // timedelta(microseconds=1)
    window_us = OFFICIAL_BASIS_HISTORY_WINDOW // timedelta(microseconds=1)
    if start_us >= end_us:
        raise ValueError(f"start_time must be before end_time, got {start_time!r}, {end_time!r}")
    if start_us % period_us or end_us % period_us:
        raise ValueError(f"start_time/end_time must be aligned to the {period!r} grid")
    if start_us < as_of_us - window_us:
        raise ValueError(
            f"start_time {start_time!r} is older than the documented 30-day basis history "
            f"as of {as_of_time!r}"
        )
    if end_us > as_of_us:
        raise ValueError(f"end_time {end_time!r} is after as_of_time {as_of_time!r}")
    if (end_us - start_us) // period_us > limit:
        raise ValueError(f"range needs more than limit={limit} records; no pagination here")

    url = build_official_basis_url(
        pair,
        contract_type,
        period,
        start_time_ms=start_us // 1000,
        end_time_ms=end_us // 1000,
        limit=limit,
    )
    rows = (fetch_rows or _fetch_rows)(url, limit, timeout)
    records: list[BinanceOfficialBasisRecord] = []
    for raw in rows:
        record = parse_binance_official_basis_record(
            raw, pair=pair, contract_type=contract_type, period=period
        )
        timestamp_us = datetime_to_epoch_us(record.timestamp)
        if records and timestamp_us <= datetime_to_epoch_us(records[-1].timestamp):
            raise ValueError(f"basis rows are unordered or duplicated at {record.timestamp!r}")
        if timestamp_us < start_us or timestamp_us > end_us:
            raise ValueError(f"basis row {record.timestamp!r} is outside the requested range")
        if timestamp_us < end_us:  # endTime is inclusive upstream; the contract is half-open
            records.append(record)
    return tuple(records)


def _fetch_rows(url: str, limit: int, timeout: float) -> list[object]:
    return fetch_usdm_json_list(url, limit=limit, timeout=timeout)


def check_official_basis_consistency(
    record: BinanceOfficialBasisRecord,
) -> OfficialBasisConsistency:
    """Check the record against its own fields; no formula is assumed to be official."""
    basis_residual = _EXACT.subtract(
        record.basis, _EXACT.subtract(record.futures_price, record.index_price)
    )
    implied_rate = _RATE.divide(record.basis, record.index_price)
    rate_residual = _RATE.subtract(record.basis_rate, implied_rate)
    return OfficialBasisConsistency(
        basis_residual=basis_residual,
        basis_rate_residual=rate_residual,
        is_consistent=basis_residual == 0
        and rate_residual.copy_abs() <= OFFICIAL_BASIS_RATE_DISPLAY_TOLERANCE,
    )
