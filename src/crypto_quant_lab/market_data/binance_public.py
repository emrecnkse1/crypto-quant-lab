"""Minimal read-only client for the Binance public Spot market-data API."""

import json
import urllib.error
import urllib.parse
import urllib.request

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.parsers import parse_binance_kline

_BASE_URL = "https://data-api.binance.vision"
_KLINES_PATH = "/api/v3/klines"
_MIN_LIMIT = 1
_MAX_LIMIT = 1000


def _build_klines_url(
    symbol: str,
    timeframe: str,
    limit: int,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> str:
    params: dict[str, str | int] = {"symbol": symbol, "interval": timeframe, "limit": limit}
    if start_time_ms is not None:
        params["startTime"] = start_time_ms
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    query = urllib.parse.urlencode(params)
    return f"{_BASE_URL}{_KLINES_PATH}?{query}"


def _validate_optional_ms(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")


def _request(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"failed to reach Binance public API: {exc}") from exc


def fetch_binance_klines(
    symbol: str,
    timeframe: str,
    limit: int = 1,
    timeout: float = 10.0,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> list[Candle]:
    """Fetch recent klines from the Binance public Spot market-data API as Candles."""
    if not symbol:
        raise ValueError("symbol cannot be empty")
    if not timeframe:
        raise ValueError("timeframe cannot be empty")
    if not (_MIN_LIMIT <= limit <= _MAX_LIMIT):
        raise ValueError(f"limit must be between {_MIN_LIMIT} and {_MAX_LIMIT}, got {limit}")
    if timeout <= 0:
        raise ValueError(f"timeout must be greater than 0, got {timeout}")
    _validate_optional_ms("start_time_ms", start_time_ms)
    _validate_optional_ms("end_time_ms", end_time_ms)
    if start_time_ms is not None and end_time_ms is not None and start_time_ms > end_time_ms:
        raise ValueError(f"start_time_ms ({start_time_ms}) must be <= end_time_ms ({end_time_ms})")

    url = _build_klines_url(
        symbol, timeframe, limit, start_time_ms=start_time_ms, end_time_ms=end_time_ms
    )
    raw_body = _request(url, timeout)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON response from Binance: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON list response, got {type(payload).__name__}")  # noqa: TRY004

    return [parse_binance_kline(raw, symbol=symbol, timeframe=timeframe) for raw in payload]
