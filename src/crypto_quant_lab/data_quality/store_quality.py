"""Store -> quality report I/O integration (DATA_QUALITY_SPEC.md Bölüm 18, Microstep 12).

Reads real rows from a canonical `HistoricalCandleStore` and composes them
with the existing pure `build_data_quality_report()`
(`data_quality/quality.py`) — no gap/alignment/PASS-FAIL logic is duplicated
here. Exchange-neutral: `exchange`/`market_type`/`symbol`/`timeframe` are
always caller-supplied, never hardcoded. Read-only — `write_batch` is never
called, and no ingestion is triggered.
"""

from datetime import datetime

from crypto_quant_lab.data_quality.quality import build_data_quality_report
from crypto_quant_lab.data_quality.report import DataQualityReport
from crypto_quant_lab.data_quality.time import calculate_effective_end
from crypto_quant_lab.storage.base import HistoricalCandleStore


def build_data_quality_report_from_store(
    store: HistoricalCandleStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
) -> DataQualityReport:
    """Query `store` over the finalized-reachable range and build its quality report.

    `effective_end` is computed once, internally, via `calculate_effective_end`
    — never accepted from the caller, so it always agrees with
    `requested_start`/`requested_end`/`as_of_time`. `store` is queried
    exactly over `[requested_start, effective_end)`, never up to
    `requested_end` — the incomplete tail must never reach quality
    evaluation, even if rows already exist there from a wider prior
    ingestion.
    """
    effective_end = calculate_effective_end(requested_start, requested_end, as_of_time, timeframe)

    rows = store.query(exchange, market_type, symbol, timeframe, requested_start, effective_end)

    observed_open_times = [record.candle.open_time for record in rows]

    return build_data_quality_report(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
        effective_end=effective_end,
        observed_open_times=observed_open_times,
    )
