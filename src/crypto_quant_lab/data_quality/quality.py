"""Pure gap/alignment quality computation (DATA_QUALITY_SPEC.md Bölüm 12, 13, 13.1).

Builds a `DataQualityReport` from the deterministic expected grid, an exact
set-difference missing-timestamp computation (never an `expected_count -
actual_count` shortcut), and independent grid-alignment detection over
caller-supplied observed `open_time`s. No store/network/SQLite access, no
wall-clock read. Missing timestamps are only ever reported (counted and
sampled) — never materialized as a synthetic/forward-filled/interpolated
observed row.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from crypto_quant_lab.data_quality.report import DataQualityReport
from crypto_quant_lab.data_quality.time import incomplete_tail_excluded_count, is_grid_aligned
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime

_MAX_SAMPLES = 20


def build_data_quality_report(
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    effective_end: datetime,
    observed_open_times: Sequence[datetime],
) -> DataQualityReport:
    """Compute a `DataQualityReport` over `[requested_start, effective_end)`.

    `observed_open_times` must be exactly the rows a caller (Microstep 12)
    already fetched for that same half-open range — every timestamp must lie
    within it and the sequence must be strictly ascending with no duplicates
    (compared as UTC instants). Violations raise `ValueError`; nothing is
    silently filtered, sorted, or deduplicated.
    """
    duration_us = candle_duration(timeframe) // timedelta(microseconds=1)

    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)
    datetime_to_epoch_us(as_of_time)  # validate timezone-awareness only
    effective_end_us = datetime_to_epoch_us(effective_end)

    if not is_grid_aligned(requested_start, timeframe):
        raise ValueError(
            f"requested_start is not aligned to the {timeframe!r} grid: {requested_start!r}"
        )
    if not is_grid_aligned(requested_end, timeframe):
        raise ValueError(
            f"requested_end is not aligned to the {timeframe!r} grid: {requested_end!r}"
        )
    if not is_grid_aligned(effective_end, timeframe):
        raise ValueError(
            f"effective_end is not aligned to the {timeframe!r} grid: {effective_end!r}"
        )

    if requested_start_us >= effective_end_us:
        raise ValueError(
            "requested_start must be strictly before effective_end, got "
            f"requested_start={requested_start!r}, effective_end={effective_end!r}"
        )
    if effective_end_us > requested_end_us:
        raise ValueError(
            "effective_end must not be after requested_end, got "
            f"effective_end={effective_end!r}, requested_end={requested_end!r}"
        )

    expected_timestamps_us: list[int] = []
    cursor_us = requested_start_us
    while cursor_us < effective_end_us:
        expected_timestamps_us.append(cursor_us)
        cursor_us += duration_us
    expected_count = len(expected_timestamps_us)

    observed_us: list[int] = []
    unaligned_us: list[int] = []
    previous_us: int | None = None
    for open_time in observed_open_times:
        open_time_us = datetime_to_epoch_us(open_time)
        if open_time_us < requested_start_us or open_time_us >= effective_end_us:
            raise ValueError(
                "observed open_time is outside the canonical "
                "[requested_start, effective_end) range: "
                f"open_time_us={open_time_us}, "
                f"requested_start_us={requested_start_us}, effective_end_us={effective_end_us}"
            )
        if previous_us is not None and open_time_us <= previous_us:
            raise ValueError(
                "observed_open_times must be strictly ascending with no duplicates: "
                f"open_time_us={open_time_us} does not follow previous_us={previous_us}"
            )
        if not is_grid_aligned(open_time, timeframe):
            unaligned_us.append(open_time_us)
        observed_us.append(open_time_us)
        previous_us = open_time_us

    actual_count = len(observed_us)

    missing_us_sorted = sorted(set(expected_timestamps_us) - set(observed_us))
    missing_count = len(missing_us_sorted)
    missing_samples = tuple(epoch_us_to_datetime(us) for us in missing_us_sorted[:_MAX_SAMPLES])

    unaligned_count = len(unaligned_us)
    unaligned_samples = tuple(epoch_us_to_datetime(us) for us in unaligned_us[:_MAX_SAMPLES])

    first_observed_open_time = observed_open_times[0] if observed_us else None
    last_observed_open_time = observed_open_times[-1] if observed_us else None

    tail_excluded_count = incomplete_tail_excluded_count(effective_end, requested_end, timeframe)

    overall_status = (
        "FAIL" if (missing_count > 0 or unaligned_count > 0 or actual_count == 0) else "PASS"
    )

    return DataQualityReport(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
        effective_end=effective_end,
        first_observed_open_time=first_observed_open_time,
        last_observed_open_time=last_observed_open_time,
        expected_count=expected_count,
        actual_count=actual_count,
        missing_count=missing_count,
        missing_samples=missing_samples,
        unaligned_count=unaligned_count,
        unaligned_samples=unaligned_samples,
        incomplete_tail_excluded_count=tail_excluded_count,
        overall_status=overall_status,
    )
