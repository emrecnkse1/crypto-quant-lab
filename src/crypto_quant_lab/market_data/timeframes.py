"""Timeframe cadence primitives for the Faz 3 data quality / ingestion layer.

Faz 3 MVP intentionally supports only "1h" and "4h" (DATA_QUALITY_SPEC.md
Bölüm 3) — both fixed-duration, unambiguous on the UTC grid. Any other
timeframe string is rejected explicitly; there is no silent normalization,
guessing, or fallback duration.
"""

from datetime import timedelta


def candle_duration(timeframe: str) -> timedelta:
    """Return the fixed duration for a Faz 3 MVP-supported timeframe.

    Only the exact strings "1h" and "4h" are supported. Any other value —
    including case variants, whitespace variants, and calendar-variable
    intervals like "1M" — raises ValueError.
    """
    if timeframe == "1h":
        return timedelta(hours=1)
    if timeframe == "4h":
        return timedelta(hours=4)
    raise ValueError(f"unsupported timeframe: {timeframe!r}; Faz 3 MVP only supports '1h', '4h'")
