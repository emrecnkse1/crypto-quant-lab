"""Binance USDⓈ-M perpetual contract-trade kline ingestion (FUNDING_RESEARCH_SPEC.md Bölüm 10).

Reuses the spot ingestion precedent unchanged: `calculate_effective_end`
(incomplete tail excluded), bounded `ConnectionError` retry, deterministic
pagination over the half-open `[requested_start, effective_end)` range with
transport widening + exact range filter, and mandatory finalized-candle
validation. Differences are only the source (`/fapi/v1/klines`), the strict
12-field parser, and the write path: candles, the dataset provenance and the
authoritative coverage interval are committed in ONE atomic
`write_ingestion_batch` call — any failure (network exhaustion, HTTP/API error,
malformed/unordered/duplicate/out-of-range/unaligned/non-finalized data,
non-advancing cursor, provenance conflict) leaves the store untouched and
records no coverage.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from crypto_quant_lab.data_quality.finalization import is_binance_historical_kline_finalized
from crypto_quant_lab.data_quality.pagination import FetchPage, paginate_historical_klines
from crypto_quant_lab.data_quality.retry import with_connection_retry
from crypto_quant_lab.data_quality.time import calculate_effective_end, is_grid_aligned
from crypto_quant_lab.market_data.binance_usdm import fetch_binance_usdm_klines
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import (
    CandleDataset,
    binance_usdm_perpetual_contract_trade_dataset,
)
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_DEFAULT_PAGE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class UsdmKlineIngestionResult:
    """What one successful ingestion actually wrote over `[requested_start, effective_end)`.

    Absent grid slots are reported, never filled: `leading_absent_count` are
    slots before the first returned candle (possibly before the symbol was
    listed — not distinguishable from the data alone), `internal_missing_count`
    are true gaps between returned candles, `trailing_absent_count` are slots
    after the last returned candle. `requested_end > effective_end` means the
    unfinished tail was excluded.
    """

    dataset: CandleDataset
    requested_start: datetime
    requested_end: datetime
    effective_end: datetime
    candle_count: int
    first_open_time: datetime | None
    last_open_time: datetime | None
    leading_absent_count: int
    internal_missing_count: int
    trailing_absent_count: int


def ingest_binance_usdm_perpetual_klines(
    store: object,
    *,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    fetch_page: FetchPage | None = None,
    max_attempts: int = 3,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
) -> UsdmKlineIngestionResult:
    """Ingest finalized USDⓈ-M perpetual contract-trade klines, atomically with provenance.

    `store` must provide `write_ingestion_batch` (e.g. `SQLiteHistoricalCandleStore`).
    `fetch_page`, when supplied, replaces the HTTP adapter as the raw (pre-retry)
    page fetcher — used by tests; it receives `start_time_ms`/`end_time_ms`.
    """
    write_ingestion_batch = getattr(store, "write_ingestion_batch", None)
    if not callable(write_ingestion_batch):
        raise TypeError("store must provide write_ingestion_batch for provenance-bound ingestion")
    dataset = binance_usdm_perpetual_contract_trade_dataset(symbol, timeframe)
    effective_end = calculate_effective_end(requested_start, requested_end, as_of_time, timeframe)

    raw_fetch_page = fetch_page
    if raw_fetch_page is None:
        raw_fetch_page = partial(fetch_binance_usdm_klines, symbol, timeframe, limit=page_limit)
    envelopes = paginate_historical_klines(
        with_connection_retry(raw_fetch_page, max_attempts=max_attempts),
        requested_start=requested_start,
        effective_end=effective_end,
        timeframe=timeframe,
    )

    records: list[HistoricalCandle] = []
    for envelope in envelopes:
        candle = envelope.candle
        if candle.symbol != symbol or candle.timeframe != timeframe:
            raise ValueError(
                f"page candle ({candle.symbol!r}, {candle.timeframe!r}) does not match "
                f"requested ({symbol!r}, {timeframe!r})"
            )
        if not is_grid_aligned(candle.open_time, timeframe):
            raise ValueError(f"candle open_time is not aligned to the {timeframe!r} grid: "
                             f"{candle.open_time!r}")  # fmt: skip
        if not is_binance_historical_kline_finalized(envelope, as_of_time):
            raise ValueError(
                f"canonical effective range contained a non-finalized candle: "
                f"open_time={candle.open_time!r}"
            )
        records.append(
            HistoricalCandle(exchange=dataset.exchange, market_type=dataset.market_type,
                             candle=candle)
        )  # fmt: skip

    write_ingestion_batch(
        records, dataset=dataset, covered_start=requested_start, covered_end=effective_end
    )

    duration_us = candle_duration(timeframe) // timedelta(microseconds=1)
    start_us = datetime_to_epoch_us(requested_start)
    end_us = datetime_to_epoch_us(effective_end)
    total_slots = (end_us - start_us) // duration_us
    if not records:
        return UsdmKlineIngestionResult(
            dataset=dataset,
            requested_start=requested_start,
            requested_end=requested_end,
            effective_end=effective_end,
            candle_count=0,
            first_open_time=None,
            last_open_time=None,
            leading_absent_count=total_slots,
            internal_missing_count=0,
            trailing_absent_count=0,
        )
    first_us = datetime_to_epoch_us(records[0].candle.open_time)
    last_us = datetime_to_epoch_us(records[-1].candle.open_time)
    span_slots = (last_us - first_us) // duration_us + 1
    return UsdmKlineIngestionResult(
        dataset=dataset,
        requested_start=requested_start,
        requested_end=requested_end,
        effective_end=effective_end,
        candle_count=len(records),
        first_open_time=records[0].candle.open_time,
        last_open_time=records[-1].candle.open_time,
        leading_absent_count=(first_us - start_us) // duration_us,
        internal_missing_count=span_slots - len(records),
        trailing_absent_count=(end_us - last_us) // duration_us - 1,
    )
