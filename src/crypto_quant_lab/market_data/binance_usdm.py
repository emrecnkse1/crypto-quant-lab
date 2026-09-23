"""Read-only Binance USDⓈ-M futures contract-trade kline adapter (FUNDING_RESEARCH_SPEC.md Bölüm 10).

`GET https://fapi.binance.com/fapi/v1/klines` — public market data, no API key.
Only contract-trade klines: mark-price, index-price, continuous-contract and
premium-index klines are different endpoints and are never fetched here.

Each response row must have exactly 12 fields (verified against a live
response): [0] open time ms (int), [1] open, [2] high, [3] low, [4] close,
[5] volume (base asset), [6] close time ms (int), [7] quote asset volume,
[8] number of trades (int), [9] taker buy base volume, [10] taker buy quote
volume, [11] ignore. Price/volume fields must be JSON strings and are parsed
directly str -> Decimal (never through float). The canonical `Candle` keeps
only OHLC + base volume; close time is kept in the `BinanceHistoricalKline`
envelope for finalization checks; quote volume, trade count and taker-buy
volumes are validated (finite, non-negative) and then intentionally
discarded — they are not squeezed into other fields.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.models import Candle

USDM_BASE_URL = "https://fapi.binance.com"
USDM_KLINES_PATH = "/fapi/v1/klines"
USDM_MAX_KLINE_LIMIT = 1500
USDM_KLINE_FIELD_COUNT = 12

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class BinanceApiError(ValueError):
    """Binance answered with an HTTP error status (not retried as a connection failure)."""


def _ms_to_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {field_name}: {value!r}")
    try:
        return _UTC_EPOCH + timedelta(milliseconds=value)
    except OverflowError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _decimal_field(value: object, field_name: str, *, non_negative: bool) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON string, got {type(value).__name__}")  # noqa: TRY004
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    if non_negative and parsed < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value!r}")
    return parsed


def parse_binance_usdm_kline(raw: object, symbol: str, timeframe: str) -> BinanceHistoricalKline:
    """Strictly convert one 12-field `/fapi/v1/klines` row into a `BinanceHistoricalKline`."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"raw kline must be a sequence, got {type(raw).__name__}")  # noqa: TRY004
    if len(raw) != USDM_KLINE_FIELD_COUNT:
        raise ValueError(
            f"raw kline must have exactly {USDM_KLINE_FIELD_COUNT} fields, got {len(raw)}"
        )
    open_time = _ms_to_datetime(raw[0], "open time")
    close_time = _ms_to_datetime(raw[6], "close time")
    open_, high, low, close = (
        _decimal_field(raw[index], name, non_negative=False)
        for index, name in ((1, "open"), (2, "high"), (3, "low"), (4, "close"))
    )
    for name, price in (("open", open_), ("high", high), ("low", low), ("close", close)):
        if price <= 0:
            raise ValueError(f"{name} must be > 0, got {price}")
    volume = _decimal_field(raw[5], "volume", non_negative=True)
    _decimal_field(raw[7], "quote asset volume", non_negative=True)
    trades = raw[8]
    if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
        raise ValueError(f"invalid number of trades: {trades!r}")
    _decimal_field(raw[9], "taker buy base volume", non_negative=True)
    _decimal_field(raw[10], "taker buy quote volume", non_negative=True)
    if not isinstance(raw[11], str):
        raise ValueError(f"ignore field must be a JSON string, got {type(raw[11]).__name__}")  # noqa: TRY004
    candle = Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    return BinanceHistoricalKline(candle=candle, close_time=close_time)


def build_usdm_klines_url(
    symbol: str, timeframe: str, *, start_time_ms: int, end_time_ms: int, limit: int
) -> str:
    """The exact public request URL (validated parameters)."""
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol cannot be empty")
    if not isinstance(timeframe, str) or not timeframe:
        raise ValueError("timeframe cannot be empty")
    for name, value in (("start_time_ms", start_time_ms), ("end_time_ms", end_time_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    if start_time_ms > end_time_ms:
        raise ValueError(f"start_time_ms ({start_time_ms}) must be <= end_time_ms ({end_time_ms})")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= USDM_MAX_KLINE_LIMIT
    ):
        raise ValueError(
            f"limit must be an int between 1 and {USDM_MAX_KLINE_LIMIT}, got {limit!r}"
        )
    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": timeframe,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": limit,
        }
    )
    return f"{USDM_BASE_URL}{USDM_KLINES_PATH}?{query}"


def _request(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:500].decode("utf-8", errors="replace")
        raise BinanceApiError(f"Binance USDⓈ-M API HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"failed to reach Binance USDⓈ-M API: {exc}") from exc


def fetch_binance_usdm_klines(
    symbol: str,
    timeframe: str,
    *,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 1000,
    timeout: float = 10.0,
) -> list[BinanceHistoricalKline]:
    """Fetch exactly one page of contract-trade klines (one request, no retry)."""
    if timeout <= 0:
        raise ValueError(f"timeout must be greater than 0, got {timeout}")
    url = build_usdm_klines_url(
        symbol, timeframe, start_time_ms=start_time_ms, end_time_ms=end_time_ms, limit=limit
    )
    raw_body = _request(url, timeout)
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON response from Binance USDⓈ-M API: {exc}") from exc
    if not isinstance(payload, list):
        raise BinanceApiError(f"expected a JSON list response, got {payload!r}"[:500])
    if len(payload) > limit:
        raise ValueError(f"page exceeded limit: got {len(payload)} rows, limit {limit}")
    return [parse_binance_usdm_kline(raw, symbol, timeframe) for raw in payload]
