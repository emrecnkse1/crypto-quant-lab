"""Binance USDⓈ-M Futures funding-rate raw parser + single-page public client.

Source-only module (FUNDING_DATA_SPEC.md Bölüm 42): parses one raw Binance
`GET /fapi/v1/fundingRate` response row into `BinanceHistoricalFundingRecord`,
and fetches exactly one page of that endpoint. No canonical mapping, no
storage, no pagination, no retry — those are later microsteps (9, 10).

Binance funding history is USDⓈ-M Futures (`fapi.binance.com`), distinct
from the Spot market-data API used by `market_data/binance_public.py`
(`data-api.binance.vision`) — kept in a separate module deliberately.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

_FUTURES_BASE_URL = "https://fapi.binance.com"
_FUNDING_RATE_PATH = "/fapi/v1/fundingRate"

_MIN_LIMIT = 1
_MAX_LIMIT = 1000

_VALID_RATE_TYPES = frozenset({"Regular", "Special"})

_REQUIRED_FIELDS = ("symbol", "fundingRate", "fundingTime", "markPrice", "rateType")

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class BinanceHistoricalFundingRecord:
    """One raw Binance funding-rate row, source vocabulary preserved.

    `mark_price`/`rate_type` are kept in Binance's own naming — the
    `mark_price -> reference_price` canonical rename happens in a later
    microstep (10), not here (FUNDING_DATA_SPEC.md Bölüm 9, 42).
    """

    symbol: str
    funding_rate: Decimal
    funding_time: datetime
    mark_price: Decimal
    rate_type: str


def _to_strict_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string, got {type(value).__name__}")  # noqa: TRY004
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return decimal_value


def _to_funding_time(raw_funding_time_ms: object) -> datetime:
    if isinstance(raw_funding_time_ms, bool) or not isinstance(raw_funding_time_ms, int):
        raise ValueError(f"invalid fundingTime: {raw_funding_time_ms!r}")  # noqa: TRY004
    if raw_funding_time_ms < 0:
        raise ValueError(f"invalid fundingTime: {raw_funding_time_ms!r}")
    try:
        return _UTC_EPOCH + timedelta(microseconds=raw_funding_time_ms * 1000)
    except OverflowError as exc:
        raise ValueError(f"invalid fundingTime: {raw_funding_time_ms!r}") from exc


def _validate_symbol(symbol: object) -> str:
    """Shared symbol validation — used by both the parser's `expected_symbol` and the client."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"symbol must be a non-empty str, got {symbol!r}")
    return symbol


def parse_binance_historical_funding_record(
    raw: object,
    *,
    expected_symbol: str,
) -> BinanceHistoricalFundingRecord:
    """Convert a single raw Binance `/fapi/v1/fundingRate` row into a record.

    Handles exactly one response object — the client maps this across a page.
    """
    _validate_symbol(expected_symbol)

    if not isinstance(raw, Mapping):
        raise ValueError(f"raw funding record must be a mapping, got {type(raw).__name__}")  # noqa: TRY004

    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"raw funding record missing required fields: {missing}")

    symbol = raw["symbol"]
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"invalid symbol: {symbol!r}")
    if symbol != expected_symbol:
        raise ValueError(f"symbol mismatch: expected {expected_symbol!r}, got {symbol!r}")

    funding_rate = _to_strict_decimal(raw["fundingRate"], "fundingRate")
    funding_time = _to_funding_time(raw["fundingTime"])

    mark_price = _to_strict_decimal(raw["markPrice"], "markPrice")
    if mark_price <= 0:
        raise ValueError(f"markPrice must be > 0, got {mark_price}")

    rate_type = raw["rateType"]
    if not isinstance(rate_type, str) or rate_type not in _VALID_RATE_TYPES:
        raise ValueError(f"unknown rateType: {rate_type!r}")

    return BinanceHistoricalFundingRecord(
        symbol=symbol,
        funding_rate=funding_rate,
        funding_time=funding_time,
        mark_price=mark_price,
        rate_type=rate_type,
    )


def _request(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"failed to reach Binance Futures API: {exc}") from exc


def _validate_time_bound(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")  # noqa: TRY004
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(f"limit must be an int, got {type(limit).__name__}")  # noqa: TRY004
    if not (_MIN_LIMIT <= limit <= _MAX_LIMIT):
        raise ValueError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}, got {limit}")
    return limit


def _build_funding_rate_url(symbol: str, start_time_ms: int, end_time_ms: int, limit: int) -> str:
    params: dict[str, str | int] = {
        "symbol": symbol,
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "limit": limit,
    }
    query = urllib.parse.urlencode(params)
    return f"{_FUTURES_BASE_URL}{_FUNDING_RATE_PATH}?{query}"


def fetch_binance_funding_rate_page(
    symbol: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    timeout: float = 10.0,
) -> list[BinanceHistoricalFundingRecord]:
    """Fetch exactly one page of Binance USDⓈ-M Futures funding-rate history.

    One request, no retry, no pagination — Microstep 9 owns cursor advance
    and bounded `ConnectionError` retry. `startTime`/`endTime` are sent
    exactly as given (Binance's range is inclusive); canonical half-open
    filtering belongs to Microstep 10.
    """
    validated_symbol = _validate_symbol(symbol)
    validated_start = _validate_time_bound("start_time_ms", start_time_ms)
    validated_end = _validate_time_bound("end_time_ms", end_time_ms)
    if validated_start > validated_end:
        raise ValueError(
            f"start_time_ms ({validated_start}) must be <= end_time_ms ({validated_end})"
        )
    validated_limit = _validate_limit(limit)

    url = _build_funding_rate_url(validated_symbol, validated_start, validated_end, validated_limit)
    raw_body = _request(url, timeout)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON response from Binance: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON list response, got {type(payload).__name__}")  # noqa: TRY004

    return [
        parse_binance_historical_funding_record(raw, expected_symbol=validated_symbol)
        for raw in payload
    ]
