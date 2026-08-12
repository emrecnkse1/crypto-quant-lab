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


def _build_klines_url(symbol: str, timeframe: str, limit: int) -> str:
    query = urllib.parse.urlencode({"symbol": symbol, "interval": timeframe, "limit": limit})
    return f"{_BASE_URL}{_KLINES_PATH}?{query}"


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

    url = _build_klines_url(symbol, timeframe, limit)
    raw_body = _request(url, timeout)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON response from Binance: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON list response, got {type(payload).__name__}")  # noqa: TRY004

    return [parse_binance_kline(raw, symbol=symbol, timeframe=timeframe) for raw in payload]
