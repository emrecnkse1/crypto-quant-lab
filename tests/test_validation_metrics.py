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
from crypto_quant_lab.validation.metrics import Stage1Metrics, compute_stage1_metrics
from crypto_quant_lab.validation.rolling import run_rolling_backtest_from_store
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
