"""Thin real-store orchestration entrypoint (BACKTEST_SPEC.md Bölüm 5, 6, 7, 35 madde 10).

`run_backtest_from_store` wires `prepare_backtest_dataset` (store -> quality
gate -> `tuple[Candle, ...]`) into `run_backtest_replay` (candle-by-candle
deterministic replay -> `BacktestResult`). No new quality/range/execution/
accounting/result logic — pure composition. `as_of_time` flows unmodified
into both calls, keeping the quality gate's finalized-data boundary and the
replay's global availability boundary aligned to the same point-in-time run.
Any exception raised by either composed function (quality gate failure,
storage error, replay/policy/cost_model error) propagates unchanged — this
module is not an error-translation layer.
"""

from datetime import datetime

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.dataset import prepare_backtest_dataset
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult
from crypto_quant_lab.backtest.policy import BacktestPolicy
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.storage.base import HistoricalCandleStore


def run_backtest_from_store(
    store: HistoricalCandleStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    config: BacktestConfig,
    policy: BacktestPolicy,
    cost_model: CostModel,
) -> BacktestResult:
    """Run a deterministic backtest over `store`'s quality-gated data for `[requested_start, requested_end)`."""
    candles = prepare_backtest_dataset(
        store,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
    )

    return run_backtest_replay(
        candles,
        as_of_time=as_of_time,
        config=config,
        policy=policy,
        cost_model=cost_model,
    )
