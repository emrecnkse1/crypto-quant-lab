"""Binance-specific historical ingestion envelope preserving raw close_time."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.parsers import parse_binance_kline

_CLOSE_TIME_INDEX = 6
_MIN_HISTORICAL_KLINE_FIELDS = _CLOSE_TIME_INDEX + 1

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class BinanceHistoricalKline:
    candle: Candle
    close_time: datetime


def _to_close_time(raw_close_time_ms: object) -> datetime:
    if isinstance(raw_close_time_ms, bool) or not isinstance(raw_close_time_ms, int):
        raise ValueError(f"invalid close_time: {raw_close_time_ms!r}")  # noqa: TRY004
    try:
        return _UTC_EPOCH + timedelta(microseconds=raw_close_time_ms * 1000)
    except OverflowError as exc:
        raise ValueError(f"invalid close_time: {raw_close_time_ms!r}") from exc


def parse_binance_historical_kline(
    raw: Sequence, symbol: str, timeframe: str
) -> BinanceHistoricalKline:
    """Convert a single raw Binance historical kline row into a BinanceHistoricalKline."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"raw kline must be a sequence, got {type(raw).__name__}")  # noqa: TRY004
    if len(raw) < _MIN_HISTORICAL_KLINE_FIELDS:
        raise ValueError(
            f"raw kline must have at least {_MIN_HISTORICAL_KLINE_FIELDS} fields, got {len(raw)}"
        )

    candle = parse_binance_kline(raw, symbol, timeframe)
    close_time = _to_close_time(raw[_CLOSE_TIME_INDEX])

    return BinanceHistoricalKline(candle=candle, close_time=close_time)
