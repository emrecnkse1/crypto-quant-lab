"""Binance transport-boundary widening + canonical exact range filter.

(DATA_QUALITY_SPEC.md Bölüm 7)

`transport_start_ms`/`transport_end_ms` convert internal UTC
epoch-microsecond boundaries into safely widened Binance millisecond request
boundaries. They are transport-layer helpers ONLY — never the source of
truth for range membership, and never rely on the exchange's own
inclusive/exclusive interpretation of its `endTime` parameter. Underfetch is
forbidden; overfetch is expected and corrected by `is_open_time_in_range`.

`is_open_time_in_range` is the canonical, exact, microsecond-precision
`[requested_start, effective_end)` membership check — the sole source of
truth for whether a candle belongs in the result. It is independent of
transport-layer widening and of pagination's `current_cursor` (a separate
concept, not implemented in this module).

No network/HTTP/pagination/ingestion code lives here.
"""

from datetime import datetime

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_MICROSECONDS_PER_MILLISECOND = 1000


def transport_start_ms(start_time: datetime) -> int:
    """Safe lower bound (widened backward, never later) for a Binance `startTime`."""
    start_us = datetime_to_epoch_us(start_time)
    return start_us // _MICROSECONDS_PER_MILLISECOND


def transport_end_ms(end_time: datetime) -> int:
    """Safe upper bound (widened forward, never earlier) for a Binance `endTime`."""
    end_us = datetime_to_epoch_us(end_time)
    quotient, remainder = divmod(end_us, _MICROSECONDS_PER_MILLISECOND)
    return quotient if remainder == 0 else quotient + 1


def is_open_time_in_range(
    open_time: datetime,
    requested_start: datetime,
    effective_end: datetime,
) -> bool:
    """Canonical, exact `requested_start <= open_time < effective_end` membership.

    This is the only correctness source for range membership — independent
    of transport-layer ms widening and of pagination's `current_cursor`.
    """
    requested_start_us = datetime_to_epoch_us(requested_start)
    effective_end_us = datetime_to_epoch_us(effective_end)

    if requested_start_us >= effective_end_us:
        raise ValueError(
            "requested_start must be strictly before effective_end, got "
            f"requested_start={requested_start!r}, effective_end={effective_end!r}"
        )

    open_time_us = datetime_to_epoch_us(open_time)
    return requested_start_us <= open_time_us < effective_end_us
