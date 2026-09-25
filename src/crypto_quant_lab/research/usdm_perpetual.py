"""Provenance-checked entry point for funding research on USDⓈ-M perpetual candles (FUNDING_RESEARCH_SPEC.md Bölüm 10).

Wraps `evaluate_funding_research_candidate` (unchanged) and refuses to start
any backtest unless, mechanically:
- the funding history is Binance `usdm_perpetual` (exchange/market/symbol),
- the candle store registers the SAME namespace as Binance USDⓈ-M perpetual
  contract-trade klines with EXACTLY the expected source — by default
  `/fapi/v1/klines`, or an explicit `contract_source` such as a declared
  synthetic label (Bölüm 19.14) (spot, mark-price, index-price,
  continuous-contract, other-source or unknown-provenance candles are
  rejected — nothing is guessed from legacy rows),
- the candle store's authoritative coverage contains every evaluation window,
- every decision instant's funding knowledge cutoff lies inside the funding
  history coverage.
Funding cash is still applied exactly once by the canonical engine.
"""

from datetime import datetime

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.research.funding_carry import (
    FundingSignalHistory,
    evaluate_funding_research_candidate,
)
from crypto_quant_lab.storage.base import HistoricalCandleStore
from crypto_quant_lab.storage.datasets import (
    BINANCE,
    BINANCE_USDM_KLINES_SOURCE,
    USDM_PERPETUAL,
    CandleCoverageInterval,
    binance_usdm_perpetual_contract_trade_dataset,
)
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.windows import TemporalWindow


def _covered(window: TemporalWindow, intervals: list[CandleCoverageInterval]) -> bool:
    cursor = window.start
    for interval in sorted(intervals, key=lambda item: (item.start_time, item.end_time)):
        if interval.start_time > cursor:
            break
        cursor = max(cursor, interval.end_time)
        if cursor >= window.end:
            return True
    return cursor >= window.end


def evaluate_usdm_perpetual_funding_research(
    candle_store: HistoricalCandleStore,
    funding_store: HistoricalFundingStore,
    history: FundingSignalHistory,
    candidate: Candidate,
    *,
    windows: tuple[TemporalWindow, ...],
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_model: FundingModel,
    contract_source: str = BINANCE_USDM_KLINES_SOURCE,
) -> Trial:
    """Validate market provenance and coverage, then run the funding research evaluation."""
    if not isinstance(history, FundingSignalHistory):
        raise TypeError(f"history must be a FundingSignalHistory, got {type(history).__name__}")
    if (history.exchange, history.market_type) != (BINANCE, USDM_PERPETUAL):
        raise ValueError(
            f"funding history must be ({BINANCE!r}, {USDM_PERPETUAL!r}), got "
            f"({history.exchange!r}, {history.market_type!r})"
        )
    query_dataset = getattr(candle_store, "query_dataset", None)
    query_coverage = getattr(candle_store, "query_coverage", None)
    if not callable(query_dataset) or not callable(query_coverage):
        raise TypeError("candle_store must expose query_dataset and query_coverage provenance")
    if not isinstance(windows, tuple) or not windows:
        raise ValueError("windows must be a non-empty tuple of TemporalWindow")
    for index, window in enumerate(windows):
        if not isinstance(window, TemporalWindow):
            raise TypeError(
                f"windows[{index}] must be a TemporalWindow, got {type(window).__name__}"
            )

    expected = binance_usdm_perpetual_contract_trade_dataset(
        history.symbol, timeframe, source=contract_source
    )
    registered = query_dataset(*expected.namespace)
    if registered is None:
        raise ValueError(
            f"candle namespace {expected.namespace!r} has no registered provenance; "
            "unknown-provenance candles are never assumed to be perpetual contract-trade klines"
        )
    if registered != expected:
        raise ValueError(
            f"candle namespace {expected.namespace!r} is registered as "
            f"price_kind={registered.price_kind!r}, source={registered.source!r}; expected "
            f"price_kind={expected.price_kind!r}, source={expected.source!r}"
        )

    first_start = min(window.start for window in windows)
    last_end = max(window.end for window in windows)
    intervals = query_coverage(*expected.namespace, first_start, last_end)
    lag = history.publication_lag
    duration = candle_duration(timeframe)
    for index, window in enumerate(windows):
        if not _covered(window, intervals):
            raise ValueError(
                f"windows[{index}] [{window.start!r}, {window.end!r}) is not inside the "
                "candle store's authoritative coverage"
            )
        first_cutoff = window.start + duration - lag
        last_cutoff = window.end - lag
        if first_cutoff < history.coverage_start or last_cutoff >= history.coverage_end:
            raise ValueError(
                f"windows[{index}] decisions need funding knowledge over "
                f"[{first_cutoff!r}, {last_cutoff!r}], outside funding history coverage "
                f"[{history.coverage_start!r}, {history.coverage_end!r})"
            )

    return evaluate_funding_research_candidate(
        candle_store,
        funding_store,
        history,
        candidate,
        windows=windows,
        timeframe=timeframe,
        as_of_time=as_of_time,
        config=config,
        cost_model=cost_model,
        funding_model=funding_model,
    )
