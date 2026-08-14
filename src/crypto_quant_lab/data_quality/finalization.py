"""Close-time consistency validation and finalized-candle decision (DATA_QUALITY_SPEC.md Bölüm 9).

Pure and offline — never reads the wall clock; `as_of_time` is always an
explicit, injected parameter. Builds on the existing `BinanceHistoricalKline`
envelope, the `candle_duration()` cadence primitive, and the lossless
epoch-microsecond codec in `storage.sqlite_codec` — no new timestamp
conversion logic is introduced here.
"""

from datetime import datetime, timedelta

from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime

_ONE_MILLISECOND_US = 1_000


def validated_close_boundary(kline: BinanceHistoricalKline) -> datetime:
    """Validate raw close_time consistency and return the expected close boundary.

    DATA_QUALITY_SPEC.md Bölüm 9: `raw_close_time + 1ms` must exactly equal
    `open_time + candle_duration(timeframe)`. Any mismatch — including
    sub-millisecond drift — raises ValueError; there is no silent correction
    or tolerance.
    """
    open_time_us = datetime_to_epoch_us(kline.candle.open_time)
    close_time_us = datetime_to_epoch_us(kline.close_time)

    duration_us = candle_duration(kline.candle.timeframe) // timedelta(microseconds=1)

    expected_close_boundary_us = open_time_us + duration_us
    raw_close_boundary_us = close_time_us + _ONE_MILLISECOND_US

    if raw_close_boundary_us != expected_close_boundary_us:
        raise ValueError(
            "close_time is inconsistent with candle cadence: "
            f"raw_close_time + 1ms = {raw_close_boundary_us}us since epoch, "
            f"expected open_time + duration = {expected_close_boundary_us}us since epoch"
        )

    return epoch_us_to_datetime(expected_close_boundary_us)


def is_binance_historical_kline_finalized(
    kline: BinanceHistoricalKline,
    as_of_time: datetime,
) -> bool:
    """Whether `kline` is finalized (closed) as of `as_of_time`.

    Close-time consistency is validated first; an inconsistent envelope
    raises ValueError rather than being reported as not-finalized.
    `as_of_time` must be timezone-aware and is compared as a UTC instant;
    the boundary itself counts as finalized (inclusive).
    """
    boundary_us = datetime_to_epoch_us(validated_close_boundary(kline))
    as_of_us = datetime_to_epoch_us(as_of_time)
    return as_of_us >= boundary_us
