"""Pure, backend-neutral serialization helpers for canonical SQLite storage.

Converts between domain values (Decimal, timezone-aware datetime) and their
lossless SQLite storage representation (TEXT, INTEGER epoch microseconds),
per HISTORICAL_DATA_SPEC.md Bölüm 14.2 and 14.3. No sqlite3, SQL, connection,
or filesystem access happens here — only deterministic conversions.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from crypto_quant_lab.storage.base import DataCorruptionError

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND


def decimal_to_text(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise TypeError(f"value must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"value must be finite, got {value!r}")
    return str(value)


def text_to_decimal(value: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"value must be a str, got {type(value).__name__}")
    try:
        decoded = Decimal(value)
    except InvalidOperation as exc:
        raise DataCorruptionError(f"stored value is not a valid Decimal: {value!r}") from exc
    if not decoded.is_finite():
        raise DataCorruptionError(f"stored value is not finite: {value!r}")
    return decoded


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def datetime_to_epoch_us(value: datetime) -> int:
    if not isinstance(value, datetime):
        raise TypeError(f"value must be a datetime, got {type(value).__name__}")
    if not _is_timezone_aware(value):
        raise ValueError("value must be timezone-aware")

    delta = value - _EPOCH
    return (
        delta.days * _MICROSECONDS_PER_DAY
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )


def epoch_us_to_datetime(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"value must be an int, got {type(value).__name__}")

    try:
        return _EPOCH + timedelta(microseconds=value)
    except (OverflowError, ValueError) as exc:
        raise DataCorruptionError(
            f"stored epoch microseconds value is out of range: {value!r}"
        ) from exc
