"""Binance Spot historical ingestion -> atomic canonical store bridge.

(DATA_QUALITY_SPEC.md Bölüm 18, Microstep 9: "Ingestion -> write_batch köprüsü")

Composes, in order: effective-range calculation, bounded connection retry,
pagination, and mandatory finalized-candle validation — over the existing
`fetch_binance_historical_klines` HTTP adapter — then performs exactly one
atomic `store.write_batch()` call. No partial historical batch is ever
written: any failure anywhere in fetch/retry/pagination/finalization leaves
`store` completely untouched. Quality reporting and gap detection are out of
scope here (deferred to a later microstep).
"""

from datetime import datetime
from functools import partial

from crypto_quant_lab.data_quality.finalization import is_binance_historical_kline_finalized
from crypto_quant_lab.data_quality.pagination import FetchPage, paginate_historical_klines
from crypto_quant_lab.data_quality.retry import with_connection_retry
from crypto_quant_lab.data_quality.time import calculate_effective_end
from crypto_quant_lab.market_data.binance_historical import BinanceHistoricalKline
from crypto_quant_lab.market_data.binance_public import fetch_binance_historical_klines
from crypto_quant_lab.storage.base import HistoricalCandle, HistoricalCandleStore

_EXCHANGE = "binance"
_MARKET_TYPE = "spot"
_HISTORICAL_FETCH_LIMIT = 1000


def _default_fetch_page(
    *, start_time_ms: int, end_time_ms: int, symbol: str, timeframe: str
) -> list[BinanceHistoricalKline]:
    return fetch_binance_historical_klines(
        symbol,
        timeframe,
        limit=_HISTORICAL_FETCH_LIMIT,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )


def ingest_binance_historical_range(
    store: HistoricalCandleStore,
    *,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    fetch_page: FetchPage | None = None,
    max_attempts: int = 3,
) -> None:
    """Ingest finalized Binance Spot klines over `[requested_start, requested_end)` into `store`.

    Pipeline: `calculate_effective_end` -> bounded-retry paginated fetch ->
    mandatory finalized-candle validation -> canonical `HistoricalCandle` ->
    a single atomic `store.write_batch()` call. `fetch_page`, when supplied,
    replaces the production Binance HTTP adapter as the *raw* (pre-retry)
    fetcher — used by tests to avoid real network access. Any failure at any
    stage (network exhaustion, malformed/inconsistent upstream data,
    pagination invariant violation, non-finalized candle in range) leaves
    `store` completely untouched.
    """
    effective_end = calculate_effective_end(requested_start, requested_end, as_of_time, timeframe)

    raw_fetch_page = fetch_page
    if raw_fetch_page is None:
        raw_fetch_page = partial(_default_fetch_page, symbol=symbol, timeframe=timeframe)

    retrying_fetch_page = with_connection_retry(raw_fetch_page, max_attempts=max_attempts)

    envelopes = paginate_historical_klines(
        retrying_fetch_page,
        requested_start=requested_start,
        effective_end=effective_end,
        timeframe=timeframe,
    )

    records: list[HistoricalCandle] = []
    for envelope in envelopes:
        if not is_binance_historical_kline_finalized(envelope, as_of_time):
            raise ValueError(
                "canonical effective range contained a non-finalized candle: "
                f"open_time={envelope.candle.open_time!r}"
            )
        records.append(
            HistoricalCandle(exchange=_EXCHANGE, market_type=_MARKET_TYPE, candle=envelope.candle)
        )

    if not records:
        return

    store.write_batch(records)
