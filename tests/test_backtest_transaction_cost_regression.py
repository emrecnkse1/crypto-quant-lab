"""Faz 5A realistic transaction-cost integration regression (COST_MODEL_SPEC.md).

Proves the real `CompositeCostModel(commission, spread, slippage)` stack
plugs into the unchanged Faz 4 replay/store pipeline with zero production
change, that reversal charges each component exactly once on the physical
`2q` fill, that no-op/last-candle signals never reach the cost model, and
that cost stays separate from (and reconciles against) realized/unrealized
PnL. Expected values below are hand-computed independently — commission/
spread/slippage rates times quantity/price, and cash/PnL/equity via
BACKTEST_SPEC.md's own formulas — never derived from `calculate_cost`,
`apply_fill`, `equity`, `unrealized_pnl`, `build_equity_point`, or
`build_backtest_result`. This file intentionally duplicates (rather than
imports) the small canonical fixture already used by
`tests/test_backtest_regression.py`, which remains untouched: that file is
Faz 4's historical `Decimal(2)`-fixed-cost checkpoint, not a shared helper
module.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from crypto_quant_lab.backtest.costs import (
    CompositeCostModel,
    ProportionalCommissionModel,
    ProportionalSlippageCostModel,
    ProportionalSpreadCostModel,
)
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.replay import run_backtest_replay
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


class _CanonicalPolicy:
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


class RecordingDelegatingCostModel:
    """Test-only spy: records each `(quantity, execution_price)` call, then delegates to a real `CostModel`."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = []

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        self.calls.append((quantity, execution_price))
        return self.delegate.calculate_cost(quantity=quantity, execution_price=execution_price)


def _canonical_candles():
    candles = []
    for hour, open_, high, low, close, volume in _CANONICAL_SPECS:
        candles.append(
            Candle(
                symbol=_SYMBOL,
                timeframe=_TIMEFRAME,
                open_time=datetime(2026, 1, 1, hour, 0, tzinfo=UTC),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return tuple(candles)


def _make_canonical_records():
    records = []
    for candle in _canonical_candles():
        records.append(
            HistoricalCandle(exchange=_EXCHANGE, market_type=_MARKET_TYPE, candle=candle)
        )
    return records


def _make_canonical_store(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "transaction_cost_regression.db")
    store.write_batch(_make_canonical_records())
    return store


def _config():
    return BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(2))


def _build_realistic_cost_model():
    return CompositeCostModel(
        components=(
            ProportionalCommissionModel(rate=Decimal("0.001")),
            ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005")),
            ProportionalSlippageCostModel(rate=Decimal("0.001")),
        )
    )


def _expected_result():
    return BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=Decimal("978.15"),
        final_equity=Decimal("978.15"),
        total_realized_pnl=Decimal(-20),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal("-21.85"),
        total_cost=Decimal("1.8500"),
        fill_count=3,
        trade_count=4,
        equity_curve=(
            EquityPoint(time=datetime(2026, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
            EquityPoint(time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), equity=Decimal("1009.45")),
            EquityPoint(time=datetime(2026, 1, 1, 13, 0, tzinfo=UTC), equity=Decimal("948.55")),
            EquityPoint(time=datetime(2026, 1, 1, 14, 0, tzinfo=UTC), equity=Decimal("978.15")),
            EquityPoint(time=datetime(2026, 1, 1, 15, 0, tzinfo=UTC), equity=Decimal("978.15")),
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


# --- TEST A: replay instrumentation (Criteria 11, 12, 13) ---


def test_realistic_composite_replay_charges_exact_physical_fills():
    recording_commission = RecordingDelegatingCostModel(
        ProportionalCommissionModel(rate=Decimal("0.001"))
    )
    recording_spread = RecordingDelegatingCostModel(
        ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    )
    recording_slippage = RecordingDelegatingCostModel(
        ProportionalSlippageCostModel(rate=Decimal("0.001"))
    )
    composite = CompositeCostModel(
        components=(recording_commission, recording_spread, recording_slippage)
    )

    result = run_backtest_replay(
        _canonical_candles(),
        as_of_time=datetime(2026, 1, 1, 15, 0, tzinfo=UTC),
        config=_config(),
        policy=_CanonicalPolicy(),
        cost_model=composite,
    )

    expected_calls = [
        (Decimal(2), Decimal(110)),
        (Decimal(4), Decimal(90)),
        (Decimal(2), Decimal(80)),
    ]
    assert recording_commission.calls == expected_calls
    assert recording_spread.calls == expected_calls
    assert recording_slippage.calls == expected_calls

    assert result.total_cost == Decimal("1.8500")
    assert result.fill_count == 3
    assert result.trade_count == 4


# --- TEST B: store-backed realistic golden (Criteria 14, 15, 19) ---


def test_realistic_transaction_cost_store_backed_matches_golden_result(tmp_path):
    store = _make_canonical_store(tmp_path)
    policy = _CanonicalPolicy()
    cost_model = _build_realistic_cost_model()

    actual = run_backtest_from_store(store, policy=policy, cost_model=cost_model, **_run_kwargs())
    store.close()

    expected = _expected_result()
    assert actual == expected

    assert actual.total_realized_pnl == Decimal(-20)
    assert actual.total_cost == Decimal("1.8500")
    assert actual.total_pnl == Decimal("-21.85")
    assert (
        actual.total_pnl
        == actual.total_realized_pnl + actual.total_unrealized_pnl - actual.total_cost
    )


# --- TEST C: determinism (Criterion 20) ---


def test_realistic_transaction_cost_store_backed_is_deterministic(tmp_path):
    store = _make_canonical_store(tmp_path)
    kwargs = _run_kwargs()

    result1 = run_backtest_from_store(
        store, policy=_CanonicalPolicy(), cost_model=_build_realistic_cost_model(), **kwargs
    )
    result2 = run_backtest_from_store(
        store, policy=_CanonicalPolicy(), cost_model=_build_realistic_cost_model(), **kwargs
    )
    store.close()

    expected = _expected_result()
    assert result1 == expected
    assert result2 == expected
    assert result1 == result2
