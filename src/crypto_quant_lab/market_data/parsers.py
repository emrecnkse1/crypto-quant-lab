"""Offline parsers converting exchange-specific raw market data into domain models."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from crypto_quant_lab.market_data.models import Candle

_MIN_KLINE_FIELDS = 6


def _to_decimal(value: object, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _to_open_time(raw_timestamp_ms: object) -> datetime:
    try:
        timestamp_ms = int(raw_timestamp_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid timestamp: {raw_timestamp_ms!r}") from exc

    seconds, milliseconds = divmod(timestamp_ms, 1000)
    try:
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=milliseconds)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError(f"invalid timestamp: {raw_timestamp_ms!r}") from exc


def parse_binance_kline(raw: Sequence, symbol: str, timeframe: str) -> Candle:
    """Convert a single raw Binance kline row into a Candle."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"raw kline must be a sequence, got {type(raw).__name__}")  # noqa: TRY004
    if len(raw) < _MIN_KLINE_FIELDS:
        raise ValueError(f"raw kline must have at least {_MIN_KLINE_FIELDS} fields, got {len(raw)}")

    open_time = _to_open_time(raw[0])
    open_price = _to_decimal(raw[1], "open")
    high_price = _to_decimal(raw[2], "high")
    low_price = _to_decimal(raw[3], "low")
    close_price = _to_decimal(raw[4], "close")
    volume = _to_decimal(raw[5], "volume")

    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
    )
