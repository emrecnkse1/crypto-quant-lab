"""Quality-gated historical dataset preparation (BACKTEST_SPEC.md Bölüm 5, 6, 30).

Reuses the existing Faz 3 quality gate
(`data_quality.store_quality.build_data_quality_report_from_store`) — no
quality logic is reimplemented here. `[requested_start, report.effective_end)`
is the single source of truth for the dataset boundary; the incomplete tail
never reaches the returned dataset. Read-only and fully offline — no
Binance/network/ingestion, no policy/cost/accounting.
"""

from datetime import datetime

from crypto_quant_lab.data_quality.store_quality import build_data_quality_report_from_store
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandleStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def prepare_backtest_dataset(
    store: HistoricalCandleStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
) -> tuple[Candle, ...]:
    """Prepare an immutable, quality-gated `Candle` dataset over `[requested_start, effective_end)`.

    Delegates entirely to the existing Faz 3 quality gate; raises
    `ValueError` if `report.overall_status != "PASS"`. The second store
    query (for the actual OHLCV data) is bounded by `report.effective_end`,
    never `requested_end` — the incomplete tail never reaches the returned
    dataset. Every row is independently re-validated at this boundary
    (non-empty, strictly ascending/unique, matching exchange/market_type/
    symbol/timeframe) rather than trusting the quality gate or the store's
    own contract blindly.
    """
    report = build_data_quality_report_from_store(
        store,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
    )

    if report.overall_status != "PASS":
        raise ValueError(
            "backtest dataset quality gate failed: overall_status="
            f"{report.overall_status!r} for exchange={exchange!r}, market_type={market_type!r}, "
            f"symbol={symbol!r}, timeframe={timeframe!r}"
        )

    rows = store.query(
        exchange, market_type, symbol, timeframe, requested_start, report.effective_end
    )

    if not rows:
        raise ValueError(
            "backtest dataset query returned no rows despite a PASS quality report: "
            f"exchange={exchange!r}, market_type={market_type!r}, symbol={symbol!r}, "
            f"timeframe={timeframe!r}"
        )

    previous_us: int | None = None
    for record in rows:
        if record.exchange != exchange:
            raise ValueError(
                f"unexpected exchange in dataset row: expected {exchange!r}, got {record.exchange!r}"
            )
        if record.market_type != market_type:
            raise ValueError(
                "unexpected market_type in dataset row: expected "
                f"{market_type!r}, got {record.market_type!r}"
            )
        if record.candle.symbol != symbol:
            raise ValueError(
                f"unexpected symbol in dataset row: expected {symbol!r}, got {record.candle.symbol!r}"
            )
        if record.candle.timeframe != timeframe:
            raise ValueError(
                "unexpected timeframe in dataset row: expected "
                f"{timeframe!r}, got {record.candle.timeframe!r}"
            )

        open_time_us = datetime_to_epoch_us(record.candle.open_time)
        if previous_us is not None and open_time_us <= previous_us:
            raise ValueError(
                "backtest dataset rows are not strictly ascending/unique: "
                f"open_time_us={open_time_us} does not follow previous_us={previous_us}"
            )
        previous_us = open_time_us

    return tuple(record.candle for record in rows)
