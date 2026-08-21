import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal, getcontext, localcontext

import pytest

from crypto_quant_lab.backtest.costs import ProportionalSpreadCostModel, ZeroCostModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import (
    FundingCoverageInterval,
    FundingEvent,
    HistoricalFundingEvent,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.metrics import (
    Stage1Metrics,
    Stage2Metrics,
    compute_periodic_returns,
    compute_stage1_metrics,
    compute_stage2_metrics,
)
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.windows import TemporalWindow

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"

T0 = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
AS_OF_TIME = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn

    def target_position(self, context):
        return self._target_fn(context)


def _point(equity, *, time=T0):
    return EquityPoint(time=time, equity=equity)


def _curve(*equities, start=T0, step=timedelta(hours=1)):
    points = []
    t = start
    for equity in equities:
        points.append(EquityPoint(time=t, equity=equity))
        t += step
    return tuple(points)


def _result(
    *,
    initial_cash=Decimal(1000),
    final_cash=None,
    final_equity=Decimal(1000),
    total_realized_pnl=Decimal(0),
    total_unrealized_pnl=Decimal(0),
    total_pnl=None,
    total_cost=Decimal(0),
    fill_count=0,
    trade_count=0,
    equity_curve=None,
):
    if final_cash is None:
        final_cash = final_equity
    if total_pnl is None:
        total_pnl = final_equity - initial_cash
    if equity_curve is None:
        equity_curve = (_point(final_equity),)
    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=final_cash,
        final_equity=final_equity,
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        total_pnl=total_pnl,
        total_cost=total_cost,
        fill_count=fill_count,
        trade_count=trade_count,
        equity_curve=equity_curve,
    )


def _make_record(open_time, *, price=Decimal(100)):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _candle_store(tmp_path, prices):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    records = []
    t = T0
    for price in prices:
        records.append(_make_record(t, price=price))
        t += timedelta(hours=1)
    store.write_batch(records)
    return store


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _run(
    store,
    *,
    policy,
    prices,
    cost_model=None,
    funding_required=False,
    funding_store=None,
    funding_model=None,
):
    requested_end = T0 + timedelta(hours=len(prices))
    return run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=requested_end,
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=policy,
        cost_model=cost_model or ZeroCostModel(),
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )


class _FakeFundingStore:
    def __init__(self, *, coverage=(), events=()):
        self._coverage = coverage
        self._events = events

    def query_events(self, *, exchange, market_type, symbol, start_time, end_time):
        return self._events

    def query_coverage(self, *, exchange, market_type, symbol, start_time, end_time):
        return self._coverage

    def close(self):
        pass


def _funding_event(event_time, *, funding_rate="0.001", reference_price="100"):
    return HistoricalFundingEvent(
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type="Regular",
        ),
    )


def _coverage(start, end):
    return FundingCoverageInterval(start_time=start, end_time=end)


# =====================================================================
# Stage1Metrics: model tests
# =====================================================================


def test_stage1_metrics_valid_construction():
    metrics = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    assert metrics.total_return == Decimal("0.05")
    assert metrics.max_drawdown == Decimal("0.1")


def test_stage1_metrics_field_preservation():
    metrics = Stage1Metrics(total_return=Decimal("-0.25"), max_drawdown=Decimal("0.4"))
    assert metrics.total_return == Decimal("-0.25")
    assert metrics.max_drawdown == Decimal("0.4")


def test_stage1_metrics_value_equality():
    a = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    b = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    assert a == b


def test_stage1_metrics_hashable():
    a = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    b = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_stage1_metrics_is_frozen():
    metrics = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    with pytest.raises(FrozenInstanceError):
        metrics.total_return = Decimal(0)


def test_stage1_metrics_is_slotted():
    metrics = Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("0.1"))
    assert not hasattr(metrics, "__dict__")


def test_stage1_metrics_rejects_non_decimal_total_return():
    with pytest.raises(TypeError, match="total_return"):
        Stage1Metrics(total_return=0.05, max_drawdown=Decimal("0.1"))


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage1_metrics_rejects_non_finite_total_return(bad):
    with pytest.raises(ValueError, match="total_return"):
        Stage1Metrics(total_return=bad, max_drawdown=Decimal("0.1"))


def test_stage1_metrics_rejects_non_decimal_max_drawdown():
    with pytest.raises(TypeError, match="max_drawdown"):
        Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=0.1)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage1_metrics_rejects_non_finite_max_drawdown(bad):
    with pytest.raises(ValueError, match="max_drawdown"):
        Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=bad)


def test_stage1_metrics_rejects_negative_max_drawdown():
    with pytest.raises(ValueError, match="max_drawdown"):
        Stage1Metrics(total_return=Decimal("0.05"), max_drawdown=Decimal("-0.01"))


def test_stage1_metrics_max_drawdown_zero_is_accepted():
    metrics = Stage1Metrics(total_return=Decimal(0), max_drawdown=Decimal(0))
    assert metrics.max_drawdown == Decimal(0)


def test_stage1_metrics_max_drawdown_above_one_is_accepted():
    metrics = Stage1Metrics(total_return=Decimal(0), max_drawdown=Decimal("1.5"))
    assert metrics.max_drawdown == Decimal("1.5")


def test_stage1_metrics_total_return_below_minus_one_is_accepted():
    metrics = Stage1Metrics(total_return=Decimal(-2), max_drawdown=Decimal(0))
    assert metrics.total_return == Decimal(-2)


# =====================================================================
# compute_stage1_metrics: input validation / fail-fast order
# =====================================================================


def test_rejects_non_backtest_result_input():
    with pytest.raises(TypeError, match="BacktestResult"):
        compute_stage1_metrics("not a result")


def test_rejects_nan_initial_cash():
    result = _result(initial_cash=Decimal("NaN"))
    with pytest.raises(ValueError, match="initial_cash must be finite"):
        compute_stage1_metrics(result)


def test_rejects_infinite_initial_cash():
    result = _result(initial_cash=Decimal("Infinity"))
    with pytest.raises(ValueError, match="initial_cash must be finite"):
        compute_stage1_metrics(result)


def test_rejects_zero_initial_cash():
    result = _result(initial_cash=Decimal(0))
    with pytest.raises(ValueError, match="initial_cash must be greater than zero"):
        compute_stage1_metrics(result)


def test_rejects_negative_initial_cash():
    result = _result(initial_cash=Decimal(-1000))
    with pytest.raises(ValueError, match="initial_cash must be greater than zero"):
        compute_stage1_metrics(result)


def test_rejects_nan_final_equity():
    result = _result(final_equity=Decimal("NaN"), equity_curve=(_point(Decimal("NaN")),))
    with pytest.raises(ValueError, match="final_equity must be finite"):
        compute_stage1_metrics(result)


def test_rejects_infinite_final_equity():
    result = _result(final_equity=Decimal("Infinity"), equity_curve=(_point(Decimal("Infinity")),))
    with pytest.raises(ValueError, match="final_equity must be finite"):
        compute_stage1_metrics(result)


def test_rejects_empty_equity_curve():
    result = _result(equity_curve=())
    with pytest.raises(ValueError, match="equity_curve must not be empty"):
        compute_stage1_metrics(result)


def test_rejects_invalid_curve_member_at_index_0():
    result = _result(equity_curve=("not a point", _point(Decimal(1000))))
    with pytest.raises(TypeError, match=r"equity_curve\[0\]"):
        compute_stage1_metrics(result)


def test_rejects_invalid_curve_member_at_later_index():
    result = _result(
        equity_curve=(_point(Decimal(1000), time=T0), "not a point"),
    )
    with pytest.raises(TypeError, match=r"equity_curve\[1\]"):
        compute_stage1_metrics(result)


def test_rejects_nan_curve_equity_with_index():
    result = _result(
        equity_curve=(
            _point(Decimal(1000), time=T0),
            _point(Decimal("NaN"), time=T0 + timedelta(hours=1)),
        )
    )
    with pytest.raises(ValueError, match=r"equity_curve\[1\]\.equity must be finite"):
        compute_stage1_metrics(result)


def test_rejects_infinite_curve_equity_with_index():
    result = _result(equity_curve=(_point(Decimal("Infinity"), time=T0),))
    with pytest.raises(ValueError, match=r"equity_curve\[0\]\.equity must be finite"):
        compute_stage1_metrics(result)


def test_rejects_duplicate_timestamps():
    result = _result(equity_curve=(_point(Decimal(1000), time=T0), _point(Decimal(1100), time=T0)))
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_stage1_metrics(result)


def test_rejects_descending_timestamps_without_silently_sorting():
    result = _result(
        equity_curve=(
            _point(Decimal(1100), time=T0 + timedelta(hours=1)),
            _point(Decimal(1000), time=T0),
        )
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_stage1_metrics(result)


def test_rejects_terminal_equity_not_matching_final_equity():
    result = _result(
        final_equity=Decimal(1000),
        equity_curve=(_point(Decimal(999), time=T0),),
    )
    with pytest.raises(ValueError, match="must equal result.final_equity"):
        compute_stage1_metrics(result)


# --- validation order, proven under multiple simultaneous violations ---


def test_order_initial_cash_finiteness_checked_before_positivity():
    # NaN is neither > 0 nor <= 0 in a meaningful sense; the finite check
    # (step 2) must fire before the positivity check (step 3) is ever
    # reached, regardless.
    result = _result(initial_cash=Decimal("NaN"))
    with pytest.raises(ValueError, match="finite"):
        compute_stage1_metrics(result)


def test_order_initial_cash_positivity_checked_before_final_equity():
    result = _result(initial_cash=Decimal(-5), final_equity=Decimal("NaN"))
    with pytest.raises(ValueError, match="initial_cash must be greater than zero"):
        compute_stage1_metrics(result)


def test_order_final_equity_finiteness_checked_before_empty_curve():
    result = _result(final_equity=Decimal("NaN"), equity_curve=())
    with pytest.raises(ValueError, match="final_equity must be finite"):
        compute_stage1_metrics(result)


def test_order_curve_member_type_checked_before_ascending_order():
    result = _result(equity_curve=("not a point", _point(Decimal(1000), time=T0)))
    with pytest.raises(TypeError, match=r"equity_curve\[0\]"):
        compute_stage1_metrics(result)


def test_order_curve_finiteness_checked_before_ascending_order():
    result = _result(
        equity_curve=(
            _point(Decimal(1000), time=T0 + timedelta(hours=1)),
            _point(Decimal("NaN"), time=T0),  # earlier timestamp AND non-finite
        )
    )
    with pytest.raises(ValueError, match="finite"):
        compute_stage1_metrics(result)


def test_order_ascending_check_fires_before_terminal_equity_match():
    result = _result(
        final_equity=Decimal(1000),
        equity_curve=(
            _point(Decimal(1100), time=T0 + timedelta(hours=1)),
            _point(Decimal(2000), time=T0),  # descending AND wrong terminal value
        ),
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_stage1_metrics(result)


# =====================================================================
# Total return
# =====================================================================


def test_total_return_breakeven_is_zero():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1000))
    assert compute_stage1_metrics(result).total_return == Decimal(0)


def test_total_return_positive():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    assert compute_stage1_metrics(result).total_return == Decimal("0.1")


def test_total_return_negative():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(900))
    assert compute_stage1_metrics(result).total_return == Decimal("-0.1")


def test_total_return_total_loss_is_minus_one():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(0))
    assert compute_stage1_metrics(result).total_return == Decimal(-1)


def test_total_return_negative_final_equity_is_below_minus_one():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(-500))
    assert compute_stage1_metrics(result).total_return == Decimal("-1.5")


def test_total_return_one_point_curve():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1050),
        equity_curve=(_point(Decimal(1050)),),
    )
    assert compute_stage1_metrics(result).total_return == Decimal("0.05")


def test_total_return_no_trade_canonical_result(tmp_path):
    store = _candle_store(tmp_path, prices=[Decimal(100), Decimal(100), Decimal(100)])
    policy = _RecordingPolicy(lambda context: PositionTarget.FLAT)
    result = _run(store, policy=policy, prices=[100, 100, 100])
    metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal(0)
    assert metrics.max_drawdown == Decimal(0)


def test_total_return_costs_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    result = _run(
        store,
        policy=_RecordingPolicy(_target),
        prices=prices,
        cost_model=ProportionalSpreadCostModel(half_spread_rate=Decimal("0.01")),
    )
    # two fills (open + close) at price 100, qty 1: cost = 1*100*0.01 = 1 each = 2 total;
    # flat price -> zero realized pnl -> final_equity = 1000 - 2 = 998
    assert result.final_equity == Decimal(998)
    metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("-0.002")


def test_total_return_funding_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    funding_time = T0 + timedelta(hours=2)  # candle1's availability; position already LONG by then
    funding_run_end = T0 + timedelta(hours=len(prices))
    funding_store = _FakeFundingStore(
        coverage=[_coverage(T0, funding_run_end)],
        events=[_funding_event(funding_time, funding_rate="0.001", reference_price="100")],
    )
    result = _run(
        store,
        policy=policy,
        prices=prices,
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    # open fill at 100 (qty 1) -> cash = 900; funding = 1*100*0.001 = 0.1 outflow
    # -> final_cash = 899.9; position still marked at 100 (flat price) -> +100
    # -> final_equity = 899.9 + 100 = 999.9
    assert result.final_equity == Decimal("999.9")
    metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("-0.0001")


def test_total_return_unrealized_mark_to_market_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100), Decimal(120)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    result = _run(store, policy=policy, prices=prices)
    # open fill at 100 (qty 1) -> cash = 900; held open, marked at final close 120
    # -> final_equity = 900 + 1*120 = 1020
    assert result.final_equity == Decimal(1020)
    metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("0.02")


def test_total_return_exact_locked_operation_order():
    # final/initial - 1 vs (final-initial)/initial differ in the last digit
    # under precision 28 for this concrete pair.
    initial_cash = Decimal(32749)
    final_equity = Decimal("354848.8181818181818181818182")
    result = _result(
        initial_cash=initial_cash,
        final_equity=final_equity,
        equity_curve=(_point(final_equity),),
    )
    metrics = compute_stage1_metrics(result)
    locked = Decimal("9.83540926995688973153933916")
    forbidden_rewrite = (final_equity - initial_cash) / initial_cash
    assert metrics.total_return == locked
    assert metrics.total_return != forbidden_rewrite


def test_total_return_non_terminating_division_under_locked_precision():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(4),
        equity_curve=(_point(Decimal(4)),),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("0.333333333333333333333333333")


def test_total_return_no_post_computation_quantization():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(4),
        equity_curve=(_point(Decimal(4)),),
    )
    metrics = compute_stage1_metrics(result)
    # the full non-terminating value is preserved exactly, not truncated/
    # rounded to something shorter like two or four decimal places
    assert metrics.total_return == Decimal("0.333333333333333333333333333")
    assert len(metrics.total_return.as_tuple().digits) >= 20


# =====================================================================
# Maximum drawdown
# =====================================================================


def test_max_drawdown_flat_curve_is_zero():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1000), Decimal(1000), Decimal(1000)),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(0)


def test_max_drawdown_monotonically_rising_curve_is_zero():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1000), Decimal(1100), Decimal(1200)),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(0)


def test_max_drawdown_immediate_first_point_loss_from_initial_cash_peak():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(900),
        equity_curve=(_point(Decimal(900)),),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal("0.1")


def test_max_drawdown_single_point_below_initial_cash():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=(_point(Decimal(500)),),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal("0.5")


def test_max_drawdown_new_peak_then_partial_decline():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1200), Decimal(1100)),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.max_drawdown == Decimal(100) / Decimal(1200)


def test_max_drawdown_multiple_drawdowns_largest_is_selected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1200), Decimal(1100), Decimal(1300), Decimal(1000)),
    )
    metrics = compute_stage1_metrics(result)
    small = Decimal(100) / Decimal(1200)
    large = Decimal(300) / Decimal(1300)
    assert large > small
    assert metrics.max_drawdown == large


def test_max_drawdown_full_decline_to_zero_is_one():
    result = _result(
        initial_cash=Decimal(100),
        final_equity=Decimal(0),
        equity_curve=(_point(Decimal(0)),),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(1)


def test_max_drawdown_recovery_after_trough_does_not_erase_maximum():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1400),
        equity_curve=_curve(Decimal(1200), Decimal(800), Decimal(1500), Decimal(1400)),
    )
    metrics = compute_stage1_metrics(result)
    trough_drawdown = Decimal(400) / Decimal(1200)
    recovery_drawdown = Decimal(100) / Decimal(1500)
    assert trough_drawdown > recovery_drawdown
    assert metrics.max_drawdown == trough_drawdown


def test_max_drawdown_final_point_is_the_maximum_trough():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(800),
        equity_curve=_curve(Decimal(1000), Decimal(900), Decimal(800)),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.max_drawdown == Decimal("0.2")


def test_max_drawdown_negative_equity_after_positive_peak_exceeds_one():
    result = _result(
        initial_cash=Decimal(100),
        final_equity=Decimal(-50),
        equity_curve=_curve(Decimal(200), Decimal(-50)),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.max_drawdown == Decimal("1.25")
    assert metrics.max_drawdown > Decimal(1)


def test_max_drawdown_one_point_equal_to_initial_cash_is_zero():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=(_point(Decimal(1000)),),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(0)


def test_max_drawdown_one_point_above_initial_cash_is_zero():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=(_point(Decimal(1100)),),
    )
    assert compute_stage1_metrics(result).max_drawdown == Decimal(0)


def test_max_drawdown_non_terminating_division_under_locked_precision():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(1),
        equity_curve=(_point(Decimal(1)),),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.max_drawdown == Decimal("0.6666666666666666666666666667")


def test_max_drawdown_no_artificial_cap():
    result = _result(
        initial_cash=Decimal(1),
        final_equity=Decimal(-1000000),
        equity_curve=(_point(Decimal(-1000000)),),
    )
    metrics = compute_stage1_metrics(result)
    assert metrics.max_drawdown > Decimal(1)


# =====================================================================
# Decimal-context determinism
# =====================================================================


def test_output_independent_of_low_ambient_precision():
    result = _result(
        initial_cash=Decimal(3), final_equity=Decimal(4), equity_curve=(_point(Decimal(4)),)
    )
    with localcontext() as ctx:
        ctx.prec = 3
        metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("0.333333333333333333333333333")


def test_output_independent_of_high_ambient_precision():
    result = _result(
        initial_cash=Decimal(3), final_equity=Decimal(4), equity_curve=(_point(Decimal(4)),)
    )
    with localcontext() as ctx:
        ctx.prec = 60
        metrics = compute_stage1_metrics(result)
    assert metrics.total_return == Decimal("0.333333333333333333333333333")


def test_output_independent_of_ambient_rounding_mode():
    result = _result(
        initial_cash=Decimal(3), final_equity=Decimal(1), equity_curve=(_point(Decimal(1)),)
    )
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        down = compute_stage1_metrics(result)
    with localcontext() as ctx:
        ctx.rounding = ROUND_UP
        up = compute_stage1_metrics(result)
    assert down == up
    assert down.max_drawdown == Decimal("0.6666666666666666666666666667")


def test_output_deterministic_across_repeated_calls_after_ambient_changes():
    result = _result(
        initial_cash=Decimal(3), final_equity=Decimal(4), equity_curve=(_point(Decimal(4)),)
    )
    first = compute_stage1_metrics(result)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_DOWN
        second = compute_stage1_metrics(result)
    third = compute_stage1_metrics(result)
    assert first == second == third


def test_ambient_global_context_untouched_by_computation():
    original_prec = getcontext().prec
    result = _result(
        initial_cash=Decimal(3), final_equity=Decimal(4), equity_curve=(_point(Decimal(4)),)
    )
    compute_stage1_metrics(result)
    assert getcontext().prec == original_prec


# =====================================================================
# Purity and compatibility
# =====================================================================


def test_input_result_unchanged_after_computation():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    before = BacktestResult(
        initial_cash=result.initial_cash,
        final_cash=result.final_cash,
        final_equity=result.final_equity,
        total_realized_pnl=result.total_realized_pnl,
        total_unrealized_pnl=result.total_unrealized_pnl,
        total_pnl=result.total_pnl,
        total_cost=result.total_cost,
        fill_count=result.fill_count,
        trade_count=result.trade_count,
        equity_curve=result.equity_curve,
    )
    compute_stage1_metrics(result)
    assert result == before


def test_deterministic_repeated_computation():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    assert compute_stage1_metrics(result) == compute_stage1_metrics(result)


def test_returned_values_are_decimal_never_float():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    metrics = compute_stage1_metrics(result)
    assert isinstance(metrics.total_return, Decimal)
    assert isinstance(metrics.max_drawdown, Decimal)
    assert not isinstance(metrics.total_return, float)


def test_backtest_result_has_no_metrics_fields():
    field_names = {f.name for f in fields(BacktestResult)}
    assert "total_return" not in field_names
    assert "max_drawdown" not in field_names
    assert "stage1_metrics" not in field_names


def test_window_result_has_no_metrics_fields():
    from crypto_quant_lab.validation.rolling import WindowResult

    field_names = {f.name for f in fields(WindowResult)}
    assert "total_return" not in field_names
    assert "max_drawdown" not in field_names
    assert "stage1_metrics" not in field_names


def test_metrics_module_does_not_import_rolling():
    import crypto_quant_lab.validation.metrics as metrics_module

    assert not hasattr(metrics_module, "rolling")
    assert not hasattr(metrics_module, "run_rolling_backtest_from_store")
    assert not hasattr(metrics_module, "WindowResult")


def test_window_result_evaluated_independently_via_rolling(tmp_path):
    prices = [Decimal(100), Decimal(110), Decimal(100), Decimal(90)]
    store = _candle_store(tmp_path, prices=prices)
    windows = (
        TemporalWindow(start=T0, end=T0 + timedelta(hours=2)),
        TemporalWindow(start=T0 + timedelta(hours=2), end=T0 + timedelta(hours=4)),
    )
    rolling_result = run_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=lambda: _RecordingPolicy(lambda context: PositionTarget.FLAT),
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=_config(),
        cost_model=ZeroCostModel(),
    )
    assert len(rolling_result) == 2
    per_window_metrics = tuple(compute_stage1_metrics(w.result) for w in rolling_result)
    assert all(m.total_return == Decimal(0) for m in per_window_metrics)
    assert all(m.max_drawdown == Decimal(0) for m in per_window_metrics)


def test_no_cross_window_aggregation_occurs():
    result_a = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    result_b = _result(initial_cash=Decimal(1000), final_equity=Decimal(900))

    metrics_a_first = compute_stage1_metrics(result_a)
    metrics_b_first = compute_stage1_metrics(result_b)
    # recompute in the opposite order: each result's metrics must depend
    # only on that result, never on prior calls or call order
    metrics_b_second = compute_stage1_metrics(result_b)
    metrics_a_second = compute_stage1_metrics(result_a)

    assert (
        metrics_a_first
        == metrics_a_second
        == Stage1Metrics(total_return=Decimal("0.1"), max_drawdown=Decimal(0))
    )
    assert (
        metrics_b_first
        == metrics_b_second
        == Stage1Metrics(total_return=Decimal("-0.1"), max_drawdown=Decimal("0.1"))
    )


def test_at_least_one_canonical_backtest_result_evaluates_successfully(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.FLAT)
    result = _run(store, policy=policy, prices=prices)
    metrics = compute_stage1_metrics(result)
    assert isinstance(metrics, Stage1Metrics)


# =====================================================================
# Non-finite computed-output rejection
# =====================================================================


def test_non_finite_computed_total_return_is_rejected():
    initial_cash = Decimal("1E-500000")
    final_equity = Decimal("1E+500000")
    result = _result(
        initial_cash=initial_cash,
        final_equity=final_equity,
        equity_curve=(_point(final_equity),),
    )
    with pytest.raises(ValueError, match="computed total_return must be finite"):
        compute_stage1_metrics(result)


def test_non_finite_computed_max_drawdown_is_rejected():
    initial_cash = Decimal("1E-500000")
    final_equity = Decimal("2E-500000")
    result = _result(
        initial_cash=initial_cash,
        final_equity=final_equity,
        equity_curve=_curve(Decimal("-1E+500000"), Decimal("2E-500000")),
    )
    with pytest.raises(ValueError, match="computed max_drawdown must be finite"):
        compute_stage1_metrics(result)


# =====================================================================
# Stage2Metrics: value-object tests
# =====================================================================


def test_stage2_metrics_valid_construction():
    metrics = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    assert metrics.mean_return == Decimal("0.01")
    assert metrics.return_stdev == Decimal("0.02")
    assert metrics.sharpe_ratio == Decimal("0.5")


def test_stage2_metrics_field_preservation():
    metrics = Stage2Metrics(
        mean_return=Decimal("-0.03"), return_stdev=Decimal("0.07"), sharpe_ratio=Decimal("-0.4")
    )
    assert metrics.mean_return == Decimal("-0.03")
    assert metrics.return_stdev == Decimal("0.07")
    assert metrics.sharpe_ratio == Decimal("-0.4")


def test_stage2_metrics_value_equality():
    a = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    b = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    assert a == b


def test_stage2_metrics_hashable():
    a = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    b = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_stage2_metrics_is_frozen():
    metrics = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    with pytest.raises(FrozenInstanceError):
        metrics.mean_return = Decimal(0)


def test_stage2_metrics_is_slotted():
    metrics = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5")
    )
    assert not hasattr(metrics, "__dict__")


def test_stage2_metrics_rejects_non_decimal_mean_return():
    with pytest.raises(TypeError, match="mean_return"):
        Stage2Metrics(mean_return=0.01, return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5"))


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage2_metrics_rejects_non_finite_mean_return(bad):
    with pytest.raises(ValueError, match="mean_return"):
        Stage2Metrics(mean_return=bad, return_stdev=Decimal("0.02"), sharpe_ratio=Decimal("0.5"))


def test_stage2_metrics_rejects_non_decimal_return_stdev():
    with pytest.raises(TypeError, match="return_stdev"):
        Stage2Metrics(mean_return=Decimal("0.01"), return_stdev=0.02, sharpe_ratio=Decimal("0.5"))


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage2_metrics_rejects_non_finite_return_stdev(bad):
    with pytest.raises(ValueError, match="return_stdev"):
        Stage2Metrics(mean_return=Decimal("0.01"), return_stdev=bad, sharpe_ratio=Decimal("0.5"))


def test_stage2_metrics_rejects_negative_return_stdev():
    with pytest.raises(ValueError, match="return_stdev"):
        Stage2Metrics(
            mean_return=Decimal("0.01"), return_stdev=Decimal("-0.01"), sharpe_ratio=Decimal("0.5")
        )


def test_stage2_metrics_zero_return_stdev_is_accepted():
    metrics = Stage2Metrics(
        mean_return=Decimal("0.01"), return_stdev=Decimal(0), sharpe_ratio=Decimal("0.5")
    )
    assert metrics.return_stdev == Decimal(0)


def test_stage2_metrics_rejects_non_decimal_sharpe_ratio():
    with pytest.raises(TypeError, match="sharpe_ratio"):
        Stage2Metrics(mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=0.5)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage2_metrics_rejects_non_finite_sharpe_ratio(bad):
    with pytest.raises(ValueError, match="sharpe_ratio"):
        Stage2Metrics(mean_return=Decimal("0.01"), return_stdev=Decimal("0.02"), sharpe_ratio=bad)


# =====================================================================
# Stage-2 core validation / fail-fast order
# (shared by compute_periodic_returns and compute_stage2_metrics)
# =====================================================================

_STAGE2_ENTRY_POINTS = (compute_periodic_returns, compute_stage2_metrics)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_non_backtest_result_input(compute_fn):
    with pytest.raises(TypeError, match="BacktestResult"):
        compute_fn("not a result")


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_nan_initial_cash(compute_fn):
    result = _result(initial_cash=Decimal("NaN"))
    with pytest.raises(ValueError, match="initial_cash must be finite"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_zero_initial_cash(compute_fn):
    result = _result(initial_cash=Decimal(0))
    with pytest.raises(ValueError, match="initial_cash must be greater than zero"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_negative_initial_cash(compute_fn):
    result = _result(initial_cash=Decimal(-1000))
    with pytest.raises(ValueError, match="initial_cash must be greater than zero"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_nan_final_equity(compute_fn):
    result = _result(final_equity=Decimal("NaN"), equity_curve=(_point(Decimal("NaN")),))
    with pytest.raises(ValueError, match="final_equity must be finite"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_empty_equity_curve(compute_fn):
    result = _result(equity_curve=())
    with pytest.raises(ValueError, match="equity_curve must not be empty"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_invalid_curve_member_at_index_0(compute_fn):
    result = _result(equity_curve=("not a point", _point(Decimal(1000))))
    with pytest.raises(TypeError, match=r"equity_curve\[0\]"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_invalid_curve_member_at_later_index(compute_fn):
    result = _result(equity_curve=(_point(Decimal(1000), time=T0), "not a point"))
    with pytest.raises(TypeError, match=r"equity_curve\[1\]"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_non_finite_curve_equity_with_index(compute_fn):
    result = _result(
        equity_curve=(
            _point(Decimal(1000), time=T0),
            _point(Decimal("NaN"), time=T0 + timedelta(hours=1)),
        )
    )
    with pytest.raises(ValueError, match=r"equity_curve\[1\]\.equity must be finite"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_duplicate_timestamps(compute_fn):
    result = _result(equity_curve=(_point(Decimal(1000), time=T0), _point(Decimal(1100), time=T0)))
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_descending_timestamps_without_silently_sorting(compute_fn):
    result = _result(
        equity_curve=(
            _point(Decimal(1100), time=T0 + timedelta(hours=1)),
            _point(Decimal(1000), time=T0),
        )
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_terminal_equity_not_matching_final_equity(compute_fn):
    result = _result(final_equity=Decimal(1000), equity_curve=(_point(Decimal(999), time=T0),))
    with pytest.raises(ValueError, match="must equal result.final_equity"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_order_initial_cash_finiteness_checked_before_positivity(compute_fn):
    result = _result(initial_cash=Decimal("NaN"))
    with pytest.raises(ValueError, match="finite"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_order_curve_finiteness_checked_before_ascending_order(compute_fn):
    result = _result(
        equity_curve=(
            _point(Decimal(1000), time=T0 + timedelta(hours=1)),
            _point(Decimal("NaN"), time=T0),  # earlier timestamp AND non-finite
        )
    )
    with pytest.raises(ValueError, match="finite"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_order_ascending_check_fires_before_terminal_equity_match(compute_fn):
    result = _result(
        final_equity=Decimal(1000),
        equity_curve=(
            _point(Decimal(1100), time=T0 + timedelta(hours=1)),
            _point(Decimal(2000), time=T0),  # descending AND wrong terminal value
        ),
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_core_validation_fires_before_denominator_check(compute_fn):
    # empty curve (core violation) alongside what would otherwise also be a
    # denominator concern (N/A here, but proves core checks run first).
    result = _result(equity_curve=())
    with pytest.raises(ValueError, match="equity_curve must not be empty"):
        compute_fn(result)


def test_stage2_no_silent_sorting_of_descending_curve():
    # a would-be-valid-if-sorted curve must still fail loudly, never be
    # silently reordered into acceptance.
    result = _result(
        final_equity=Decimal(1100),
        equity_curve=(
            _point(Decimal(1100), time=T0 + timedelta(hours=1)),
            _point(Decimal(1000), time=T0),
        ),
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        compute_periodic_returns(result)


# =====================================================================
# Positive-denominator validation
# =====================================================================


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_zero_intermediate_denominator(compute_fn):
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(0), Decimal(500)),
    )
    with pytest.raises(ValueError, match=r"equity_curve\[0\]\.equity must be greater than zero"):
        compute_fn(result)


@pytest.mark.parametrize("compute_fn", _STAGE2_ENTRY_POINTS)
def test_stage2_rejects_negative_intermediate_denominator(compute_fn):
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(-100), Decimal(500)),
    )
    with pytest.raises(ValueError, match=r"equity_curve\[0\]\.equity must be greater than zero"):
        compute_fn(result)


def test_stage2_denominator_error_identifies_curve_and_return_index():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(1100), Decimal(0), Decimal(500)),
    )
    with pytest.raises(
        ValueError,
        match=r"equity_curve\[1\]\.equity must be greater than zero to compute return index 2",
    ):
        compute_periodic_returns(result)


def test_stage2_terminal_equity_need_not_be_positive():
    # the terminal point is never a denominator; a total-loss/negative
    # terminal equity is legal here exactly as it is for Stage-1.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(-50),
        equity_curve=_curve(Decimal(1100), Decimal(-50)),
    )
    returns = compute_periodic_returns(result)
    assert len(returns) == 2
    assert returns[1] < Decimal(-1)


def test_stage2_no_partial_tuple_on_denominator_failure():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(500),
        equity_curve=_curve(Decimal(1100), Decimal(0), Decimal(500)),
    )
    with pytest.raises(ValueError):
        compute_periodic_returns(result)
    # a failed call raises rather than returning any tuple at all; there is
    # no partial-result object to inspect, which is itself the guarantee.


# =====================================================================
# compute_periodic_returns: formula and observation-count behavior
# =====================================================================


def test_periodic_returns_flat_curve():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1000), Decimal(1000), Decimal(1000)),
    )
    assert compute_periodic_returns(result) == (Decimal(0), Decimal(0), Decimal(0))


def test_periodic_returns_rising_curve():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1210),
        equity_curve=_curve(Decimal(1100), Decimal(1210)),
    )
    assert compute_periodic_returns(result) == (Decimal("0.1"), Decimal("0.1"))


def test_periodic_returns_falling_curve():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(810),
        equity_curve=_curve(Decimal(900), Decimal(810)),
    )
    assert compute_periodic_returns(result) == (Decimal("-0.1"), Decimal("-0.1"))


def test_periodic_returns_mixed():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1155),
        equity_curve=_curve(Decimal(1100), Decimal(1050), Decimal(1155)),
    )
    returns = compute_periodic_returns(result)
    r1 = Decimal(1100) / Decimal(1000) - Decimal(1)
    r2 = Decimal(1050) / Decimal(1100) - Decimal(1)
    r3 = Decimal(1155) / Decimal(1050) - Decimal(1)
    assert returns == (r1, r2, r3)


def test_periodic_returns_one_point_curve():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1050),
        equity_curve=(_point(Decimal(1050)),),
    )
    assert compute_periodic_returns(result) == (Decimal("0.05"),)


def test_periodic_returns_exact_n_points_produce_n_returns():
    equities = [Decimal(1000 + 10 * i) for i in range(1, 8)]
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=equities[-1],
        equity_curve=_curve(*equities),
    )
    returns = compute_periodic_returns(result)
    assert len(returns) == len(equities) == 7


def test_periodic_returns_initial_cash_first_return_baseline():
    result = _result(
        initial_cash=Decimal(2000),
        final_equity=Decimal(1800),
        equity_curve=(_point(Decimal(1800)),),
    )
    returns = compute_periodic_returns(result)
    assert returns[0] == Decimal(1800) / Decimal(2000) - Decimal(1)
    assert returns[0] == Decimal("-0.1")


def test_periodic_returns_terminal_equity_zero_is_minus_one():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(0),
        equity_curve=_curve(Decimal(500), Decimal(0)),
    )
    returns = compute_periodic_returns(result)
    assert returns[-1] == Decimal(-1)


def test_periodic_returns_terminal_negative_equity_below_minus_one():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(-200),
        equity_curve=_curve(Decimal(500), Decimal(-200)),
    )
    returns = compute_periodic_returns(result)
    assert returns[-1] == Decimal(-200) / Decimal(500) - Decimal(1)
    assert returns[-1] < Decimal(-1)


def test_periodic_returns_exact_locked_operation_order():
    # same concrete pair Stage-1's total-return proof uses: final/previous - 1
    # vs (final-previous)/previous differ in the last digit under precision 28.
    previous_equity = Decimal(32749)
    current_equity = Decimal("354848.8181818181818181818182")
    result = _result(
        initial_cash=previous_equity,
        final_equity=current_equity,
        equity_curve=(_point(current_equity),),
    )
    returns = compute_periodic_returns(result)
    locked = Decimal("9.83540926995688973153933916")
    forbidden_rewrite = (current_equity - previous_equity) / previous_equity
    assert returns[0] == locked
    assert returns[0] != forbidden_rewrite


def test_periodic_returns_non_terminating_division_under_locked_precision():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(4),
        equity_curve=(_point(Decimal(4)),),
    )
    returns = compute_periodic_returns(result)
    assert returns[0] == Decimal("0.333333333333333333333333333")


def test_periodic_returns_no_post_computation_quantization():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(4),
        equity_curve=(_point(Decimal(4)),),
    )
    returns = compute_periodic_returns(result)
    assert len(returns[0].as_tuple().digits) >= 20


def test_periodic_returns_are_decimal_never_float():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    returns = compute_periodic_returns(result)
    assert all(isinstance(r, Decimal) for r in returns)
    assert not any(isinstance(r, float) for r in returns)


def test_periodic_returns_result_is_immutable_tuple():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    returns = compute_periodic_returns(result)
    assert isinstance(returns, tuple)


def test_periodic_returns_irregular_ascending_timestamps_accepted():
    equity_curve = (
        _point(Decimal(1050), time=T0),
        _point(Decimal(1100), time=T0 + timedelta(minutes=7)),
        _point(Decimal(1200), time=T0 + timedelta(days=3)),
    )
    result = _result(
        initial_cash=Decimal(1000), final_equity=Decimal(1200), equity_curve=equity_curve
    )
    returns = compute_periodic_returns(result)
    assert len(returns) == 3
    assert returns[1] == Decimal(1100) / Decimal(1050) - Decimal(1)
    assert returns[2] == Decimal(1200) / Decimal(1100) - Decimal(1)


def test_periodic_returns_costs_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 1 else PositionTarget.FLAT

    result = _run(
        store,
        policy=_RecordingPolicy(_target),
        prices=prices,
        cost_model=ProportionalSpreadCostModel(half_spread_rate=Decimal("0.01")),
    )
    assert result.final_equity == Decimal(998)
    returns = compute_periodic_returns(result)
    cumulative = Decimal(1)
    for r in returns:
        cumulative *= Decimal(1) + r
    stage1_total_return = compute_stage1_metrics(result).total_return
    assert cumulative - Decimal(1) == stage1_total_return


def test_periodic_returns_funding_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    funding_time = T0 + timedelta(hours=2)
    funding_run_end = T0 + timedelta(hours=len(prices))
    funding_store = _FakeFundingStore(
        coverage=[_coverage(T0, funding_run_end)],
        events=[_funding_event(funding_time, funding_rate="0.001", reference_price="100")],
    )
    result = _run(
        store,
        policy=policy,
        prices=prices,
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    assert result.final_equity == Decimal("999.9")
    returns = compute_periodic_returns(result)
    cumulative = Decimal(1)
    for r in returns:
        cumulative *= Decimal(1) + r
    stage1_total_return = compute_stage1_metrics(result).total_return
    assert cumulative - Decimal(1) == stage1_total_return


def test_periodic_returns_unrealized_mark_to_market_already_reflected(tmp_path):
    prices = [Decimal(100), Decimal(100), Decimal(100), Decimal(120)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    result = _run(store, policy=policy, prices=prices)
    assert result.final_equity == Decimal(1020)
    returns = compute_periodic_returns(result)
    cumulative = Decimal(1)
    for r in returns:
        cumulative *= Decimal(1) + r
    stage1_total_return = compute_stage1_metrics(result).total_return
    assert cumulative - Decimal(1) == stage1_total_return


# =====================================================================
# Evaluation-domain inheritance (Bölüm 15.10)
# =====================================================================


def test_context_candles_produce_no_return_observations(tmp_path):
    prices = [Decimal(100), Decimal(105), Decimal(100), Decimal(90), Decimal(95), Decimal(110)]
    store = _candle_store(tmp_path, prices=prices)
    evaluation_start = T0 + timedelta(hours=2)  # first 2 candles are context-only
    result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=T0 + timedelta(hours=len(prices)),
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.FLAT),
        cost_model=ZeroCostModel(),
        evaluation_start=evaluation_start,
    )
    # 4 evaluation candles (indices 2..5); context candles contribute zero
    # equity points and therefore zero return observations.
    assert len(result.equity_curve) == 4
    returns = compute_periodic_returns(result)
    assert len(returns) == 4


def test_first_return_uses_first_real_evaluation_equity_point(tmp_path):
    prices = [Decimal(100), Decimal(105), Decimal(100), Decimal(90)]
    store = _candle_store(tmp_path, prices=prices)
    evaluation_start = T0 + timedelta(hours=1)
    result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=T0 + timedelta(hours=len(prices)),
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.FLAT),
        cost_model=ZeroCostModel(),
        evaluation_start=evaluation_start,
    )
    returns = compute_periodic_returns(result)
    assert returns[0] == result.equity_curve[0].equity / result.initial_cash - Decimal(1)


def test_no_fabricated_point_at_exactly_evaluation_start(tmp_path):
    prices = [Decimal(100), Decimal(105), Decimal(100)]
    store = _candle_store(tmp_path, prices=prices)
    evaluation_start = T0 + timedelta(hours=1)
    result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=T0 + timedelta(hours=len(prices)),
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.FLAT),
        cost_model=ZeroCostModel(),
        evaluation_start=evaluation_start,
    )
    # the first real point is the first evaluation candle's availability
    # instant (evaluation_start + candle_duration), never evaluation_start
    # itself.
    assert result.equity_curve[0].time == evaluation_start + timedelta(hours=1)
    assert all(point.time != evaluation_start for point in result.equity_curve)


def test_context_aware_result_not_diluted_versus_equivalent_zero_context(tmp_path):
    prices = [Decimal(100), Decimal(105), Decimal(100), Decimal(90)]
    store = _candle_store(tmp_path, prices=prices)
    evaluation_start = T0 + timedelta(hours=2)

    context_aware_result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=T0,
        requested_end=T0 + timedelta(hours=len(prices)),
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.FLAT),
        cost_model=ZeroCostModel(),
        evaluation_start=evaluation_start,
    )
    zero_context_result = run_backtest_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        requested_start=evaluation_start,
        requested_end=T0 + timedelta(hours=len(prices)),
        as_of_time=AS_OF_TIME,
        config=_config(),
        policy=_RecordingPolicy(lambda context: PositionTarget.FLAT),
        cost_model=ZeroCostModel(),
    )
    assert compute_periodic_returns(context_aware_result) == compute_periodic_returns(
        zero_context_result
    )
    assert not any(r == Decimal(0) for r in compute_periodic_returns(context_aware_result)[:0])
    # explicit dilution check: the context-aware series has exactly as many
    # returns as evaluation candles, never two extra zero-returns for the
    # two context candles.
    assert len(compute_periodic_returns(context_aware_result)) == 2


def test_stage2_apis_do_not_accept_evaluation_start():
    periodic_params = inspect.signature(compute_periodic_returns).parameters
    stage2_params = inspect.signature(compute_stage2_metrics).parameters
    assert "evaluation_start" not in periodic_params
    assert "evaluation_start" not in stage2_params


def test_metrics_module_has_no_second_evaluation_boundary_filter():
    # No function in metrics.py declares or accepts an `evaluation_start`
    # parameter (bare mentions in docstrings explaining the design are
    # expected and legitimate — this checks callable signatures only).
    import crypto_quant_lab.validation.metrics as metrics_module

    for name, member in vars(metrics_module).items():
        if not inspect.isfunction(member) or member.__module__ != metrics_module.__name__:
            continue
        assert "evaluation_start" not in inspect.signature(member).parameters, name


# =====================================================================
# Statistics: mean, sample standard deviation, Sharpe
# =====================================================================


def test_stage2_known_arithmetic_mean():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1100), Decimal(1000), Decimal(1000)),
    )
    r1 = Decimal(1100) / Decimal(1000) - Decimal(1)
    r2 = Decimal(1000) / Decimal(1100) - Decimal(1)
    r3 = Decimal(0)
    expected_mean = (r1 + r2 + r3) / Decimal(3)
    metrics = compute_stage2_metrics(result)
    assert metrics.mean_return == expected_mean


def test_stage2_known_exact_sample_standard_deviation():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    returns = compute_periodic_returns(result)
    n = len(returns)
    mean = sum(returns, Decimal(0)) / Decimal(n)
    squared_deviation_sum = sum(((r - mean) * (r - mean) for r in returns), Decimal(0))
    expected_stdev = (squared_deviation_sum / Decimal(n - 1)).sqrt()
    metrics = compute_stage2_metrics(result)
    assert metrics.return_stdev == expected_stdev


def test_stage2_uses_sample_not_population_standard_deviation():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    returns = compute_periodic_returns(result)
    n = len(returns)
    mean = sum(returns, Decimal(0)) / Decimal(n)
    squared_deviation_sum = sum(((r - mean) * (r - mean) for r in returns), Decimal(0))
    population_stdev = (squared_deviation_sum / Decimal(n)).sqrt()
    sample_stdev = (squared_deviation_sum / Decimal(n - 1)).sqrt()
    assert population_stdev != sample_stdev
    metrics = compute_stage2_metrics(result)
    assert metrics.return_stdev == sample_stdev
    assert metrics.return_stdev != population_stdev


def test_stage2_default_risk_free_sharpe():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    metrics = compute_stage2_metrics(result)
    assert metrics.sharpe_ratio == metrics.mean_return / metrics.return_stdev
    assert metrics.sharpe_ratio != Decimal(0)


def test_stage2_explicit_non_zero_risk_free():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    risk_free = Decimal("0.01")
    metrics = compute_stage2_metrics(result, risk_free_per_period=risk_free)
    assert metrics.sharpe_ratio == (metrics.mean_return - risk_free) / metrics.return_stdev


def test_stage2_negative_sharpe():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(900),
        equity_curve=_curve(Decimal(950), Decimal(850), Decimal(900)),
    )
    metrics = compute_stage2_metrics(result)
    assert metrics.sharpe_ratio < Decimal(0)


def test_stage2_sharpe_is_dimensionless_non_annualized():
    stage2_params = inspect.signature(compute_stage2_metrics).parameters
    assert "periods_per_year" not in stage2_params
    assert set(stage2_params) == {"result", "risk_free_per_period"}


def test_stage2_no_annualization_multiplier_applied():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    metrics = compute_stage2_metrics(result)
    unannualized = metrics.mean_return / metrics.return_stdev
    assert metrics.sharpe_ratio == unannualized


def test_stage2_minimum_two_returns_required():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=(_point(Decimal(1100)),),
    )
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_stage2_metrics(result)


def test_stage2_one_return_accepted_by_periodic_returns_but_not_stage2():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=(_point(Decimal(1100)),),
    )
    returns = compute_periodic_returns(result)
    assert len(returns) == 1
    with pytest.raises(ValueError, match="at least two periodic returns"):
        compute_stage2_metrics(result)


def test_stage2_all_zero_returns_rejected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1000),
        equity_curve=_curve(Decimal(1000), Decimal(1000), Decimal(1000)),
    )
    with pytest.raises(
        ValueError, match="return_stdev must be greater than zero to compute sharpe_ratio"
    ):
        compute_stage2_metrics(result)


def test_stage2_equal_non_zero_returns_rejected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1210),
        equity_curve=_curve(Decimal(1100), Decimal(1210)),
    )
    with pytest.raises(
        ValueError, match="return_stdev must be greater than zero to compute sharpe_ratio"
    ):
        compute_stage2_metrics(result)


def test_stage2_metrics_with_zero_stdev_remains_directly_constructible():
    metrics = Stage2Metrics(
        mean_return=Decimal("0.1"), return_stdev=Decimal(0), sharpe_ratio=Decimal(0)
    )
    assert metrics.return_stdev == Decimal(0)


def test_stage2_rejects_non_decimal_risk_free():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    with pytest.raises(TypeError, match="risk_free_per_period"):
        compute_stage2_metrics(result, risk_free_per_period=0.01)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_stage2_rejects_non_finite_risk_free(bad):
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    with pytest.raises(ValueError, match="risk_free_per_period must be finite"):
        compute_stage2_metrics(result, risk_free_per_period=bad)


def test_stage2_sharpe_exact_locked_operation_order():
    # subtraction-then-division vs a forbidden division-first rewrite differ
    # under precision 28 for this concrete triple.
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    risk_free = Decimal("0.0001234567890123456789012345")
    metrics = compute_stage2_metrics(result, risk_free_per_period=risk_free)
    locked = metrics.mean_return - risk_free
    locked_sharpe = locked / metrics.return_stdev
    forbidden_rewrite = (
        metrics.mean_return / metrics.return_stdev - risk_free / metrics.return_stdev
    )
    assert metrics.sharpe_ratio == locked_sharpe
    assert metrics.sharpe_ratio != forbidden_rewrite


def test_stage2_non_terminating_square_root_under_locked_precision():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    metrics = compute_stage2_metrics(result)
    assert len(metrics.return_stdev.as_tuple().digits) >= 20


# =====================================================================
# Decimal-context determinism
# =====================================================================


def test_stage2_output_independent_of_low_ambient_precision():
    result = _result(
        initial_cash=Decimal(3),
        final_equity=Decimal(11),
        equity_curve=_curve(Decimal(4), Decimal(7), Decimal(11)),
    )
    with localcontext() as ctx:
        ctx.prec = 3
        returns = compute_periodic_returns(result)
        metrics = compute_stage2_metrics(result)
    assert len(returns[0].as_tuple().digits) >= 20
    assert len(metrics.return_stdev.as_tuple().digits) >= 20


def test_stage2_output_independent_of_high_ambient_precision():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    with localcontext() as ctx:
        ctx.prec = 60
        metrics_low = compute_stage2_metrics(result)
    metrics_default = compute_stage2_metrics(result)
    assert metrics_low == metrics_default


def test_stage2_output_independent_of_ambient_rounding_mode():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        down = compute_stage2_metrics(result)
    with localcontext() as ctx:
        ctx.rounding = ROUND_UP
        up = compute_stage2_metrics(result)
    assert down == up


def test_stage2_output_deterministic_across_repeated_calls_after_ambient_changes():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    first = compute_stage2_metrics(result)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_DOWN
        second = compute_stage2_metrics(result)
    third = compute_stage2_metrics(result)
    assert first == second == third


def test_stage2_ambient_global_context_untouched_by_computation():
    original_prec = getcontext().prec
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    compute_stage2_metrics(result)
    assert getcontext().prec == original_prec


def test_stage2_matches_locked_precision_28_round_half_even():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    metrics = compute_stage2_metrics(result)
    r1 = Decimal(1100) / Decimal(1000) - Decimal(1)
    r2 = Decimal(900) / Decimal(1100) - Decimal(1)
    r3 = Decimal(1200) / Decimal(900) - Decimal(1)
    expected_mean = (r1 + r2 + r3) / Decimal(3)
    assert metrics.mean_return == expected_mean
    assert len(metrics.mean_return.as_tuple().digits) <= 28


# =====================================================================
# Computed non-finite outputs
# =====================================================================


def test_non_finite_computed_periodic_return_is_rejected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal("1E+500000"),
        equity_curve=_curve(Decimal("1E-500000"), Decimal("1E+500000")),
    )
    with pytest.raises(ValueError, match="computed periodic return at index 1 must be finite"):
        compute_periodic_returns(result)


def test_non_finite_return_sum_or_mean_is_rejected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal("1E+500000"),
        equity_curve=_curve(Decimal("1E-500000"), Decimal("1E+500000")),
    )
    with pytest.raises(ValueError, match="computed return_sum must be finite"):
        compute_stage2_metrics(result)


def test_non_finite_squared_deviation_sum_is_rejected():
    result = _result(
        initial_cash=Decimal(1),
        final_equity=Decimal("0.01"),
        equity_curve=_curve(Decimal("1E+700000"), Decimal("0.01")),
    )
    with pytest.raises(ValueError, match="computed squared_deviation_sum must be finite"):
        compute_stage2_metrics(result)


def test_non_finite_standard_deviation_is_mathematically_unreachable_given_finite_variance():
    # sample_variance.sqrt() of any finite non-negative Decimal is itself
    # always finite under this context (sqrt roughly halves the exponent,
    # which can never push a finite value past Emax). Every public-input
    # path that would make return_stdev non-finite necessarily makes
    # squared_deviation_sum/sample_variance non-finite FIRST, and that
    # earlier check (Bölüm 15.15/15.17 ordering) always intercepts before
    # a "computed return_stdev must be finite" message could ever fire.
    # This is proven directly here rather than fabricated via an impossible
    # fixture.
    with localcontext() as ctx:
        ctx.prec = 28
        variance = Decimal("1E+999900")
        assert variance.is_finite()
        stdev = variance.sqrt()
        assert stdev.is_finite()


def test_non_finite_sharpe_ratio_is_rejected():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1100),
        equity_curve=_curve(Decimal(1100), Decimal(1210), Decimal(1100)),
    )
    huge_risk_free = Decimal("1E+1000010")
    with pytest.raises(ValueError, match="computed sharpe_ratio must be finite"):
        compute_stage2_metrics(result, risk_free_per_period=huge_risk_free)


# =====================================================================
# Purity and compatibility
# =====================================================================


def test_stage2_input_result_unchanged_after_computation():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    before = BacktestResult(
        initial_cash=result.initial_cash,
        final_cash=result.final_cash,
        final_equity=result.final_equity,
        total_realized_pnl=result.total_realized_pnl,
        total_unrealized_pnl=result.total_unrealized_pnl,
        total_pnl=result.total_pnl,
        total_cost=result.total_cost,
        fill_count=result.fill_count,
        trade_count=result.trade_count,
        equity_curve=result.equity_curve,
    )
    compute_periodic_returns(result)
    compute_stage2_metrics(result)
    assert result == before


def test_stage2_deterministic_repeated_computation():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    assert compute_periodic_returns(result) == compute_periodic_returns(result)
    assert compute_stage2_metrics(result) == compute_stage2_metrics(result)


def test_stage2_direct_backtest_result_supported(tmp_path):
    prices = [Decimal(100), Decimal(110), Decimal(100), Decimal(120)]
    store = _candle_store(tmp_path, prices=prices)
    policy = _RecordingPolicy(lambda context: PositionTarget.LONG)
    result = _run(store, policy=policy, prices=prices)
    metrics = compute_stage2_metrics(result)
    assert isinstance(metrics, Stage2Metrics)


def test_stage2_independent_window_result_supported(tmp_path):
    prices = [
        Decimal(100),
        Decimal(110),
        Decimal(100),
        Decimal(90),
        Decimal(100),
        Decimal(130),
        Decimal(90),
        Decimal(150),
    ]
    store = _candle_store(tmp_path, prices=prices)
    windows = (
        TemporalWindow(start=T0, end=T0 + timedelta(hours=4)),
        TemporalWindow(start=T0 + timedelta(hours=4), end=T0 + timedelta(hours=8)),
    )
    rolling_result = run_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=lambda: _RecordingPolicy(lambda context: PositionTarget.LONG),
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=_config(),
        cost_model=ZeroCostModel(),
    )
    assert len(rolling_result) == 2
    for window_result in rolling_result:
        assert isinstance(window_result, WindowResult)
        metrics = compute_stage2_metrics(window_result.result)
        assert isinstance(metrics, Stage2Metrics)


def test_stage2_no_cross_window_aggregation_occurs():
    result_a = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    result_b = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(900),
        equity_curve=_curve(Decimal(950), Decimal(850), Decimal(900)),
    )
    metrics_a_first = compute_stage2_metrics(result_a)
    metrics_b_first = compute_stage2_metrics(result_b)
    metrics_b_second = compute_stage2_metrics(result_b)
    metrics_a_second = compute_stage2_metrics(result_a)
    assert metrics_a_first == metrics_a_second
    assert metrics_b_first == metrics_b_second
    assert metrics_a_first != metrics_b_first


def test_stage2_no_candidate_trial_aggregation_symbols_exist():
    import crypto_quant_lab.validation.metrics as metrics_module

    assert not hasattr(metrics_module, "aggregate_candidates")
    assert not hasattr(metrics_module, "select_candidate")
    assert not hasattr(metrics_module, "Candidate")
    assert not hasattr(metrics_module, "Trial")


def test_backtest_result_has_no_stage2_fields():
    field_names = {f.name for f in fields(BacktestResult)}
    assert "mean_return" not in field_names
    assert "return_stdev" not in field_names
    assert "sharpe_ratio" not in field_names
    assert "stage2_metrics" not in field_names


def test_window_result_has_no_stage2_fields():
    field_names = {f.name for f in fields(WindowResult)}
    assert "mean_return" not in field_names
    assert "return_stdev" not in field_names
    assert "sharpe_ratio" not in field_names
    assert "stage2_metrics" not in field_names


def test_metrics_module_still_does_not_import_rolling():
    import crypto_quant_lab.validation.metrics as metrics_module

    assert not hasattr(metrics_module, "rolling")
    assert not hasattr(metrics_module, "run_rolling_backtest_from_store")
    assert not hasattr(metrics_module, "WindowResult")


def test_stage1_and_stage2_are_independently_callable():
    result = _result(
        initial_cash=Decimal(1000),
        final_equity=Decimal(1200),
        equity_curve=_curve(Decimal(1100), Decimal(900), Decimal(1200)),
    )
    stage1 = compute_stage1_metrics(result)
    stage2 = compute_stage2_metrics(result)
    assert isinstance(stage1, Stage1Metrics)
    assert isinstance(stage2, Stage2Metrics)


def test_existing_stage1_outputs_unchanged_by_stage2_addition():
    result = _result(initial_cash=Decimal(1000), final_equity=Decimal(1100))
    assert compute_stage1_metrics(result) == Stage1Metrics(
        total_return=Decimal("0.1"), max_drawdown=Decimal(0)
    )
