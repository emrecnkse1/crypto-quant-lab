"""Exchange-neutral feature availability / anti-lookahead contract (DATA_QUALITY_SPEC.md Bölüm 10).

Bir candle'ın **open dahil hiçbir** OHLCV alanı,
`candle.open_time + candle_duration(timeframe)` anından **önce** feature/
decision tarafından kullanılabilir kabul edilmez. This is exchange-neutral —
it depends only on the canonical `Candle`, never on raw Binance `close_time`
or the ingestion-boundary finalization contract (`data_quality/finalization.py`).

Pure and offline — never reads the wall clock; `as_of_time` is always an
explicit, injected parameter. Reuses the existing `candle_duration()` cadence
primitive and the lossless epoch-microsecond codec — no new timestamp
conversion logic.
"""

from datetime import datetime, timedelta

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime


def feature_availability_time(candle: Candle) -> datetime:
    """The exact UTC-aware instant from which `candle`'s OHLCV becomes usable.

    `candle.open_time + candle_duration(candle.timeframe)` — the *entire*
    candle (open included) is unavailable before this instant, no exception.
    """
    open_us = datetime_to_epoch_us(candle.open_time)
    duration_us = candle_duration(candle.timeframe) // timedelta(microseconds=1)
    return epoch_us_to_datetime(open_us + duration_us)


def is_candle_available_for_features(candle: Candle, as_of_time: datetime) -> bool:
    """Whether `candle`'s OHLCV is usable as of `as_of_time` (boundary inclusive)."""
    availability_us = datetime_to_epoch_us(feature_availability_time(candle))
    as_of_us = datetime_to_epoch_us(as_of_time)
    return as_of_us >= availability_us
