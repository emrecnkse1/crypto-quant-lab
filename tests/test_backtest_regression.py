"""Authoritative Faz 4 golden end-to-end regression (BACKTEST_SPEC.md Bölüm 35 madde 11).

Expected values are hand-computed from BACKTEST_SPEC.md's formulas, not from
production code — do not replace the hard-coded oracle below with
production-helper calculations (e.g. `apply_fill`, `build_backtest_result`).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

_EXCHANGE = "binance"
_MARKET_TYPE = "spot"
_SYMBOL = "BTCUSDT"
_TIMEFRAME = "1h"

_CANONICAL_SPECS = (
    (10, Decimal(100), Decimal(110), Decimal(95), Decimal(105), Decimal(10)),
    (11, Decimal(110), Decimal(120), Decimal(100), Decimal(115), Decimal(11)),
    (12, Decimal(90), Decimal(100), Decimal(85), Decimal(95), Decimal(12)),
    (13, Decimal(80), Decimal(90), Decimal(75), Decimal(85), Decimal(13)),
    (14, Decimal(120), Decimal(130), Decimal(110), Decimal(125), Decimal(14)),
)


class _RegressionPolicy:
    """Deterministic, context-length-driven target sequence for the canonical scenario."""

    _TARGETS_BY_LENGTH: ClassVar[dict[int, PositionTarget]] = {
        1: PositionTarget.LONG,
        2: PositionTarget.SHORT,
        3: PositionTarget.FLAT,
        4: PositionTarget.FLAT,
        5: PositionTarget.LONG,
    }

    def __init__(self):
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        length = len(context.candles)
        if length not in self._TARGETS_BY_LENGTH:
            raise AssertionError(f"unexpected context length: {length}")
        return self._TARGETS_BY_LENGTH[length]


class _FixedRecordingCostModel:
    """Deterministic Decimal(2)-per-fill test double — not a realistic cost model."""

    def __init__(self, cost=Decimal(2)):
        self._cost = cost
        self.calls = []

    def calculate_cost(self, *, quantity, execution_price):
        self.calls.append((quantity, execution_price))
        return self._cost


def _make_canonical_records():
    records = []
    for hour, open_, high, low, close, volume in _CANONICAL_SPECS:
        candle = Candle(
            symbol=_SYMBOL,
            timeframe=_TIMEFRAME,
            open_time=datetime(2026, 1, 1, hour, 0, tzinfo=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
        records.append(
            HistoricalCandle(exchange=_EXCHANGE, market_type=_MARKET_TYPE, candle=candle)
        )
    return records


def _make_canonical_store(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "regression.db")
    store.write_batch(_make_canonical_records())
    return store


def _config():
    return BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(2))


def _expected_result():
    return BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=Decimal(974),
        final_equity=Decimal(974),
        total_realized_pnl=Decimal(-20),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(-26),
        total_cost=Decimal(6),
        fill_count=3,
        trade_count=4,
        equity_curve=(
            EquityPoint(time=datetime(2026, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
            EquityPoint(time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), equity=Decimal(1008)),
            EquityPoint(time=datetime(2026, 1, 1, 13, 0, tzinfo=UTC), equity=Decimal(946)),
            EquityPoint(time=datetime(2026, 1, 1, 14, 0, tzinfo=UTC), equity=Decimal(974)),
            EquityPoint(time=datetime(2026, 1, 1, 15, 0, tzinfo=UTC), equity=Decimal(974)),
        ),
    )


def _run_kwargs():
    return {
        "exchange": _EXCHANGE,
        "market_type": _MARKET_TYPE,
        "symbol": _SYMBOL,
        "timeframe": _TIMEFRAME,
        "requested_start": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        "requested_end": datetime(2026, 1, 1, 15, 0, tzinfo=UTC),
        "as_of_time": datetime(2026, 1, 1, 15, 0, tzinfo=UTC),
        "config": _config(),
    }


def test_canonical_store_backed_backtest_matches_golden_result(tmp_path):
    store = _make_canonical_store(tmp_path)
    policy = _RegressionPolicy()
    cost_model = _FixedRecordingCostModel()

    actual = run_backtest_from_store(store, policy=policy, cost_model=cost_model, **_run_kwargs())
    store.close()

    assert actual == _expected_result()
    assert [len(context.candles) for context in policy.contexts] == [1, 2, 3, 4, 5]
    assert cost_model.calls == [
        (Decimal(2), Decimal(110)),
        (Decimal(4), Decimal(90)),
        (Decimal(2), Decimal(80)),
    ]


def test_canonical_store_backed_backtest_is_deterministic(tmp_path):
    store = _make_canonical_store(tmp_path)
    kwargs = _run_kwargs()

    result1 = run_backtest_from_store(
        store, policy=_RegressionPolicy(), cost_model=_FixedRecordingCostModel(), **kwargs
    )
    result2 = run_backtest_from_store(
        store, policy=_RegressionPolicy(), cost_model=_FixedRecordingCostModel(), **kwargs
    )
    store.close()

    expected = _expected_result()
    assert result1 == expected
    assert result2 == expected
    assert result1 == result2
