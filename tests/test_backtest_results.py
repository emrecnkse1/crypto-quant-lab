from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.accounting import AccountState
from crypto_quant_lab.backtest.models import BacktestResult, EquityPoint
from crypto_quant_lab.backtest.results import (
    build_backtest_result,
    build_equity_point,
    trade_count_for_transition,
)
from crypto_quant_lab.market_data.models import Candle


def _candle(open_time, *, open_, high, low, close, timeframe="1h"):
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal(1),
    )


def _flat_state(cash=Decimal(1000), realized_pnl=Decimal(0)):
    return AccountState(
        cash=cash, position_quantity=Decimal(0), average_entry_price=None, realized_pnl=realized_pnl
    )


def _long_state(
    cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100), realized_pnl=Decimal(0)
):
    return AccountState(
        cash=cash, position_quantity=quantity, average_entry_price=entry, realized_pnl=realized_pnl
    )


def _short_state(
    cash=Decimal(1100), quantity=Decimal(-1), entry=Decimal(100), realized_pnl=Decimal(0)
):
    return AccountState(
        cash=cash, position_quantity=quantity, average_entry_price=entry, realized_pnl=realized_pnl
    )


# --- build_equity_point: mark price ---


def test_equity_point_flat_uses_close():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(90),
        high=Decimal(150),
        low=Decimal(80),
        close=Decimal(100),
    )
    point = build_equity_point(_flat_state(cash=Decimal(1000)), candle=candle)
    assert point.equity == Decimal(1000)


def test_equity_point_long_exact():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(90),
        high=Decimal(150),
        low=Decimal(80),
        close=Decimal(120),
    )
    point = build_equity_point(
        _long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)), candle=candle
    )
    assert point.equity == Decimal(1020)


def test_equity_point_short_exact():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(90),
        high=Decimal(150),
        low=Decimal(80),
        close=Decimal(80),
    )
    point = build_equity_point(
        _short_state(cash=Decimal(1100), quantity=Decimal(-1), entry=Decimal(100)), candle=candle
    )
    assert point.equity == Decimal(1020)


def test_equity_point_ignores_open_high_low():
    # If build_equity_point mistakenly used open/high/low instead of close,
    # this would not match — open=1, high=100000, low=1, close=120.
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(1),
        high=Decimal(100000),
        low=Decimal(1),
        close=Decimal(120),
    )
    point = build_equity_point(
        _long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)), candle=candle
    )
    assert point.equity == Decimal(1020)


# --- build_equity_point: time semantics ---


def test_equity_point_time_is_availability_boundary_1h():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
    )
    point = build_equity_point(_flat_state(), candle=candle)
    assert point.time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    assert point.time != candle.open_time


def test_equity_point_time_is_availability_boundary_4h():
    candle = _candle(
        datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
        open_=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        timeframe="4h",
    )
    point = build_equity_point(_flat_state(), candle=candle)
    assert point.time == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_equity_point_time_non_utc_aware_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    candle = _candle(
        datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
        open_=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
    )
    point = build_equity_point(_flat_state(), candle=candle)
    assert point.time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


# --- build_equity_point: type validation ---


def test_equity_point_rejects_invalid_state_type():
    candle = _candle(
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        open_=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
    )
    with pytest.raises(TypeError, match="state"):
        build_equity_point("not a state", candle=candle)


def test_equity_point_rejects_invalid_candle_type():
    with pytest.raises(TypeError, match="candle"):
        build_equity_point(_flat_state(), candle="not a candle")


# --- trade_count_for_transition ---


@pytest.mark.parametrize(
    "old_quantity,new_quantity,expected",
    [
        (Decimal(0), Decimal(1), 1),
        (Decimal(0), Decimal(-1), 1),
        (Decimal(1), Decimal(0), 1),
        (Decimal(-1), Decimal(0), 1),
        (Decimal(1), Decimal(-1), 2),
        (Decimal(-1), Decimal(1), 2),
        (Decimal(0), Decimal(0), 0),
        (Decimal(1), Decimal(1), 0),
        (Decimal(-1), Decimal(-1), 0),
    ],
)
def test_trade_count_for_transition_legal(old_quantity, new_quantity, expected):
    assert trade_count_for_transition(old_quantity, new_quantity) == expected


@pytest.mark.parametrize(
    "old_quantity,new_quantity",
    [
        (Decimal(1), Decimal(2)),
        (Decimal(2), Decimal(1)),
        (Decimal(-1), Decimal(-2)),
        (Decimal(-2), Decimal(-1)),
    ],
)
def test_trade_count_for_transition_illegal_same_direction_is_rejected(old_quantity, new_quantity):
    with pytest.raises(ValueError):
        trade_count_for_transition(old_quantity, new_quantity)


@pytest.mark.parametrize("invalid_value", [1.0, 1, True])
def test_trade_count_for_transition_rejects_invalid_old_quantity_type(invalid_value):
    with pytest.raises(TypeError, match="old_quantity"):
        trade_count_for_transition(invalid_value, Decimal(1))


@pytest.mark.parametrize("invalid_value", [1.0, 1, True])
def test_trade_count_for_transition_rejects_invalid_new_quantity_type(invalid_value):
    with pytest.raises(TypeError, match="new_quantity"):
        trade_count_for_transition(Decimal(0), invalid_value)


# --- build_backtest_result: NO TRADE ---


def test_no_trade_result():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1000)),
        final_mark_price=Decimal(100),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=(),
    )
    assert result.final_cash == Decimal(1000)
    assert result.final_equity == Decimal(1000)
    assert result.total_realized_pnl == Decimal(0)
    assert result.total_unrealized_pnl == Decimal(0)
    assert result.total_pnl == Decimal(0)
    assert result.total_cost == Decimal(0)
    assert result.fill_count == 0
    assert result.trade_count == 0
    assert result.equity_curve == ()


def test_no_trade_result_with_consistent_flat_curve():
    curve = (
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
        EquityPoint(time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), equity=Decimal(1000)),
    )
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1000)),
        final_mark_price=Decimal(100),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=curve,
    )
    assert result.equity_curve == curve


# --- build_backtest_result: closed long/short ---


def test_closed_profitable_long_result():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1010), realized_pnl=Decimal(10)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.final_cash == Decimal(1010)
    assert result.final_equity == Decimal(1010)
    assert result.total_realized_pnl == Decimal(10)
    assert result.total_unrealized_pnl == Decimal(0)
    assert result.total_pnl == Decimal(10)


def test_closed_losing_long_result():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(990), realized_pnl=Decimal(-10)),
        final_mark_price=Decimal(90),
        total_cost=Decimal(0),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.total_realized_pnl == Decimal(-10)
    assert result.total_pnl == Decimal(-10)


def test_closed_profitable_short_result():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1010), realized_pnl=Decimal(10)),
        final_mark_price=Decimal(90),
        total_cost=Decimal(0),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.total_realized_pnl == Decimal(10)
    assert result.total_pnl == Decimal(10)


def test_closed_losing_short_result():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(990), realized_pnl=Decimal(-10)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.total_realized_pnl == Decimal(-10)
    assert result.total_pnl == Decimal(-10)


# --- build_backtest_result: open position at end ---


def test_open_long_profitable_at_end():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=(),
    )
    assert result.total_unrealized_pnl == Decimal(10)
    assert result.final_equity == Decimal(1010)
    assert result.final_cash != result.final_equity
    assert result.total_pnl == Decimal(10)


def test_open_long_losing_at_end():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)),
        final_mark_price=Decimal(90),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=(),
    )
    assert result.total_unrealized_pnl == Decimal(-10)
    assert result.final_equity == Decimal(990)
    assert result.total_pnl == Decimal(-10)


def test_open_short_profitable_at_end():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_short_state(cash=Decimal(1100), quantity=Decimal(-1), entry=Decimal(100)),
        final_mark_price=Decimal(90),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=(),
    )
    assert result.total_unrealized_pnl == Decimal(10)
    assert result.final_equity == Decimal(1010)
    assert result.final_cash != result.final_equity
    assert result.total_pnl == Decimal(10)


def test_open_short_losing_at_end():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_short_state(cash=Decimal(1100), quantity=Decimal(-1), entry=Decimal(100)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=(),
    )
    assert result.total_unrealized_pnl == Decimal(-10)
    assert result.final_equity == Decimal(990)
    assert result.total_pnl == Decimal(-10)


# --- build_backtest_result: cost reconciliation ---


def test_cost_reconciliation_succeeds_when_total_cost_matches_state():
    # open @100 cost=1, close @110 cost=1: cash = 1000 - 100 - 1 + 110 - 1 = 1008
    final_state = _flat_state(cash=Decimal(1008), realized_pnl=Decimal(10))
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=final_state,
        final_mark_price=Decimal(110),
        total_cost=Decimal(2),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.total_cost == Decimal(2)
    assert result.total_pnl == Decimal(8)


def test_cost_reconciliation_fails_when_total_cost_mismatches_state():
    final_state = _flat_state(cash=Decimal(1008), realized_pnl=Decimal(10))
    with pytest.raises(ValueError, match="consistency invariant"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=final_state,
            final_mark_price=Decimal(110),
            total_cost=Decimal(1),  # wrong: does not match final_state.cash
            fill_count=2,
            trade_count=2,
            equity_curve=(),
        )


# --- build_backtest_result: negative cost / rebate ---


def test_negative_total_cost_rebate_is_accepted():
    # rebate=-1 on both fills: cash = 1000 - 100 + 1 + 110 + 1 = 1012
    final_state = _flat_state(cash=Decimal(1012), realized_pnl=Decimal(10))
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=final_state,
        final_mark_price=Decimal(110),
        total_cost=Decimal(-2),
        fill_count=2,
        trade_count=2,
        equity_curve=(),
    )
    assert result.total_cost == Decimal(-2)
    assert result.total_pnl == Decimal(12)


# --- build_backtest_result: equity curve ordering ---


def test_equity_curve_strictly_ascending_is_accepted():
    curve = (
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
        EquityPoint(time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), equity=Decimal(1000)),
    )
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1000)),
        final_mark_price=Decimal(100),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=curve,
    )
    assert result.equity_curve == curve


def test_equity_curve_duplicate_timestamp_is_rejected():
    curve = (
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
    )
    with pytest.raises(ValueError, match="ascending"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=_flat_state(cash=Decimal(1000)),
            final_mark_price=Decimal(100),
            total_cost=Decimal(0),
            fill_count=0,
            trade_count=0,
            equity_curve=curve,
        )


def test_equity_curve_backward_timestamp_is_rejected():
    curve = (
        EquityPoint(time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), equity=Decimal(1000)),
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),
    )
    with pytest.raises(ValueError, match="ascending"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=_flat_state(cash=Decimal(1000)),
            final_mark_price=Decimal(100),
            total_cost=Decimal(0),
            fill_count=0,
            trade_count=0,
            equity_curve=curve,
        )


def test_equity_curve_non_tuple_is_rejected():
    curve = [EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000))]
    with pytest.raises(TypeError, match="equity_curve"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=_flat_state(cash=Decimal(1000)),
            final_mark_price=Decimal(100),
            total_cost=Decimal(0),
            fill_count=0,
            trade_count=0,
            equity_curve=curve,
        )


def test_equity_curve_non_equity_point_element_is_rejected():
    curve = ("not an equity point",)
    with pytest.raises(TypeError, match="equity_curve"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=_flat_state(cash=Decimal(1000)),
            final_mark_price=Decimal(100),
            total_cost=Decimal(0),
            fill_count=0,
            trade_count=0,
            equity_curve=curve,
        )


# --- build_backtest_result: final curve value consistency ---


def test_final_curve_value_matching_final_equity_is_accepted():
    curve = (EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1010)),)
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=curve,
    )
    assert result.equity_curve == curve


def test_final_curve_value_mismatch_is_rejected():
    curve = (EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(999)),)
    with pytest.raises(ValueError, match="final point"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state=_long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)),
            final_mark_price=Decimal(110),
            total_cost=Decimal(0),
            fill_count=1,
            trade_count=1,
            equity_curve=curve,
        )


def test_empty_equity_curve_is_allowed_even_with_open_position():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)),
        final_mark_price=Decimal(110),
        total_cost=Decimal(0),
        fill_count=1,
        trade_count=1,
        equity_curve=(),
    )
    assert result.equity_curve == ()


# --- build_backtest_result: immutability / type ---


def test_result_is_backtest_result_instance():
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1000)),
        final_mark_price=Decimal(100),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=(),
    )
    assert isinstance(result, BacktestResult)


def test_result_preserves_equity_curve_tuple():
    curve = (EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(1000)),)
    result = build_backtest_result(
        initial_cash=Decimal(1000),
        final_state=_flat_state(cash=Decimal(1000)),
        final_mark_price=Decimal(100),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=curve,
    )
    assert result.equity_curve is curve


# --- build_backtest_result: type validation ---


def test_build_backtest_result_rejects_invalid_final_state_type():
    with pytest.raises(TypeError, match="final_state"):
        build_backtest_result(
            initial_cash=Decimal(1000),
            final_state="not a state",
            final_mark_price=Decimal(100),
            total_cost=Decimal(0),
            fill_count=0,
            trade_count=0,
            equity_curve=(),
        )
