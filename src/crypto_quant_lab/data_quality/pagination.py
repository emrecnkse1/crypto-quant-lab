"""Historical kline pagination: ordering, cursor progress, canonical range filter.

(DATA_QUALITY_SPEC.md Bölüm 15)

Pure orchestration over an injected `fetch_page` callable — no network, no
retry, no finalized filtering, no wall-clock read. `fetch_page` stands in for
a future Binance HTTP adapter; here it is only a callable so tests can inject
deterministic, offline page fixtures. Reuses the existing transport-boundary
helpers (`transport_start_ms`, `transport_end_ms`, `is_open_time_in_range`)
from `data_quality.transport` — no new µs<->ms conversion or range-membership
logic is introduced here.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from crypto_quant_lab.data_quality.transport import (
    is_open_time_in_range,
    transport_end_ms,
    transport_start_ms,
)
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime

FetchPage = Callable[..., Sequence[BinanceHistoricalKline]]


def _validate_page_ordering_and_cursor(
    page: Sequence[BinanceHistoricalKline], current_cursor_us: int
) -> list[int]:
    """Validate strictly-ascending order and the cursor-progress invariant.

    Returns each candle's open_time in epoch microseconds, in page order.
    Raises ValueError on duplicate/backward timestamps or on any candle
    behind `current_cursor_us` — this is pagination page-progress
    validation, not the canonical range-membership filter.
    """
    open_times_us: list[int] = []
    previous_us: int | None = None
    for kline in page:
        open_time_us = datetime_to_epoch_us(kline.candle.open_time)
        if open_time_us < current_cursor_us:
            raise ValueError(
                "raw candle open_time is behind current_cursor: "
                f"open_time_us={open_time_us}, current_cursor_us={current_cursor_us}"
            )
        if previous_us is not None and open_time_us <= previous_us:
            raise ValueError(
                "raw page is not strictly ascending: open_time_us="
                f"{open_time_us} does not follow previous open_time_us={previous_us}"
            )
        open_times_us.append(open_time_us)
        previous_us = open_time_us
    return open_times_us


def paginate_historical_klines(
    fetch_page: FetchPage,
    *,
    requested_start: datetime,
    effective_end: datetime,
    timeframe: str,
) -> list[BinanceHistoricalKline]:
    """Page through `BinanceHistoricalKline` batches for `[requested_start, effective_end)`.

    Stops when `current_cursor >= effective_end` or when `fetch_page` returns
    a legitimate empty page — never on a row-count heuristic. Gaps in the
    cadence grid are not pagination errors (see DATA_QUALITY_SPEC.md Bölüm
    15/16); only ordering and cursor progress are enforced here.
    """
    requested_start_us = datetime_to_epoch_us(requested_start)
    effective_end_us = datetime_to_epoch_us(effective_end)
    if requested_start_us >= effective_end_us:
        raise ValueError(
            "requested_start must be strictly before effective_end, got "
            f"requested_start={requested_start!r}, effective_end={effective_end!r}"
        )

    duration_us = candle_duration(timeframe) // timedelta(microseconds=1)

    result: list[BinanceHistoricalKline] = []
    current_cursor_us = requested_start_us

    while current_cursor_us < effective_end_us:
        current_cursor = epoch_us_to_datetime(current_cursor_us)
        page = fetch_page(
            start_time_ms=transport_start_ms(current_cursor),
            end_time_ms=transport_end_ms(effective_end),
        )

        if not page:
            break

        open_times_us = _validate_page_ordering_and_cursor(page, current_cursor_us)

        for kline in page:
            if is_open_time_in_range(kline.candle.open_time, requested_start, effective_end):
                result.append(kline)

        last_validated_raw_open_time_us = open_times_us[-1]
        next_cursor_us = last_validated_raw_open_time_us + duration_us

        if next_cursor_us <= current_cursor_us:
            raise ValueError(
                "pagination cursor failed to advance: "
                f"next_cursor_us={next_cursor_us}, current_cursor_us={current_cursor_us}"
            )

        current_cursor_us = next_cursor_us

    return result
