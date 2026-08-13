"""Pure grid/range/finalization time primitives (DATA_QUALITY_SPEC.md Bölüm 4, 5, 6).

Builds on `market_data.timeframes.candle_duration()` and the existing,
tested, lossless epoch-microsecond codec in `storage.sqlite_codec` — no new
float/timestamp-based time conversion is introduced here. This module does
not perform any I/O and never reads the wall clock; `as_of_time` is always
an explicit, injected parameter (DATA_QUALITY_SPEC.md Bölüm 6).
"""

from datetime import datetime

from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime

_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND


def _duration_us(timeframe: str) -> int:
    duration = candle_duration(timeframe)
    return (
        duration.days * _MICROSECONDS_PER_DAY
        + duration.seconds * _MICROSECONDS_PER_SECOND
        + duration.microseconds
    )


def is_grid_aligned(value: datetime, timeframe: str) -> bool:
    """Whether `value` falls exactly on the UTC-epoch grid for `timeframe`."""
    duration_us = _duration_us(timeframe)
    value_us = datetime_to_epoch_us(value)
    return value_us % duration_us == 0


def latest_closed_boundary(as_of_time: datetime, timeframe: str) -> datetime:
    """The latest grid boundary at or before `as_of_time` (the last fully-closed candle's close)."""
    duration_us = _duration_us(timeframe)
    as_of_us = datetime_to_epoch_us(as_of_time)
    boundary_us = (as_of_us // duration_us) * duration_us
    return epoch_us_to_datetime(boundary_us)


def calculate_effective_end(
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    timeframe: str,
) -> datetime:
    """The finalized-reachable end of `[requested_start, requested_end)` as of `as_of_time`.

    DATA_QUALITY_SPEC.md Bölüm 5: requested_start/requested_end must be
    timezone-aware and grid-aligned (no silent floor/ceil); as_of_time only
    needs to be timezone-aware. Raises ValueError if the requested range is
    invalid/unaligned, or if nothing in the range can be finalized yet.
    """
    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)

    if requested_start_us >= requested_end_us:
        raise ValueError(
            "requested_start must be strictly before requested_end, got "
            f"requested_start={requested_start!r}, requested_end={requested_end!r}"
        )

    if not is_grid_aligned(requested_start, timeframe):
        raise ValueError(
            f"requested_start is not aligned to the {timeframe!r} grid: {requested_start!r}"
        )
    if not is_grid_aligned(requested_end, timeframe):
        raise ValueError(
            f"requested_end is not aligned to the {timeframe!r} grid: {requested_end!r}"
        )

    closed_boundary_us = datetime_to_epoch_us(latest_closed_boundary(as_of_time, timeframe))
    effective_end_us = min(requested_end_us, closed_boundary_us)

    if effective_end_us <= requested_start_us:
        raise ValueError(
            "nothing in the requested range can be finalized yet: effective_end "
            f"would not be after requested_start={requested_start!r} as of {as_of_time!r}"
        )

    return epoch_us_to_datetime(effective_end_us)


def incomplete_tail_excluded_count(
    effective_end: datetime,
    requested_end: datetime,
    timeframe: str,
) -> int:
    """Number of grid slots in `[effective_end, requested_end)` — not yet finalized, not missing."""
    if not is_grid_aligned(effective_end, timeframe):
        raise ValueError(
            f"effective_end is not aligned to the {timeframe!r} grid: {effective_end!r}"
        )
    if not is_grid_aligned(requested_end, timeframe):
        raise ValueError(
            f"requested_end is not aligned to the {timeframe!r} grid: {requested_end!r}"
        )

    effective_end_us = datetime_to_epoch_us(effective_end)
    requested_end_us = datetime_to_epoch_us(requested_end)

    if requested_end_us < effective_end_us:
        raise ValueError(
            "requested_end must not be before effective_end, got "
            f"requested_end={requested_end!r}, effective_end={effective_end!r}"
        )

    duration_us = _duration_us(timeframe)
    return (requested_end_us - effective_end_us) // duration_us
