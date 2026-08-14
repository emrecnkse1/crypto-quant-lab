from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.accounting import AccountState
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.execution import (
    ExecutionResult,
    execute_target_on_next_candle,
    target_quantity_for,
)
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.market_data.models import Candle

_SIGNAL_TIME = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
_NEXT_TIME = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
_CONFIG = BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))


def _signal_candle(open_time=_SIGNAL_TIME, timeframe="1h", symbol="BTCUSDT"):
    # Deliberately close to 100, far from next-candle prices, so a bug that
    # accidentally uses signal_candle's OHLC instead of next_candle.open
    # fails loudly.
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal(100),
        high=Decimal(105),
        low=Decimal(95),
        close=Decimal(102),
        volume=Decimal(1),
    )


def _next_candle(open_time=_NEXT_TIME, timeframe="1h", symbol="BTCUSDT", open_price=Decimal(500)):
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=open_price,
        high=open_price + Decimal(5),
        low=open_price - Decimal(5),
        close=open_price + Decimal(2),
        volume=Decimal(1),
    )


def _flat_state():
    return AccountState(
        cash=Decimal(1000),
        position_quantity=Decimal(0),
        average_entry_price=None,
        realized_pnl=Decimal(0),
    )


def _long_state():
    return AccountState(
        cash=Decimal(900),
        position_quantity=Decimal(1),
        average_entry_price=Decimal(100),
        realized_pnl=Decimal(0),
    )


def _short_state():
    return AccountState(
        cash=Decimal(1100),
        position_quantity=Decimal(-1),
        average_entry_price=Decimal(100),
        realized_pnl=Decimal(0),
    )


class _RecordingCostModel:
    def __init__(self, cost=Decimal(0)):
        self._cost = cost
        self.calls = []

    def calculate_cost(self, *, quantity, execution_price):
        self.calls.append({"quantity": quantity, "execution_price": execution_price})
        return self._cost


def _execute(state, target, cost_model=None, signal=None, next_c=None):
    return execute_target_on_next_candle(
        state,
        target=target,
        config=_CONFIG,
        signal_candle=signal if signal is not None else _signal_candle(),
        next_candle=next_c if next_c is not None else _next_candle(),
        cost_model=cost_model if cost_model is not None else _RecordingCostModel(),
    )


# --- target_quantity_for ---


def test_target_quantity_for_long():
    assert target_quantity_for(PositionTarget.LONG, Decimal(1)) == Decimal(1)


def test_target_quantity_for_flat():
    assert target_quantity_for(PositionTarget.FLAT, Decimal(1)) == Decimal(0)


def test_target_quantity_for_short():
    assert target_quantity_for(PositionTarget.SHORT, Decimal(1)) == Decimal(-1)


def test_target_quantity_for_rejects_invalid_target_type():
    with pytest.raises(TypeError, match="target"):
        target_quantity_for("LONG", Decimal(1))


@pytest.mark.parametrize("invalid_value", [1.0, 1, True])
def test_target_quantity_for_rejects_invalid_position_quantity_type(invalid_value):
    with pytest.raises(TypeError, match="position_quantity"):
        target_quantity_for(PositionTarget.LONG, invalid_value)


def test_target_quantity_for_rejects_zero_position_quantity():
    with pytest.raises(ValueError, match="position_quantity"):
        target_quantity_for(PositionTarget.LONG, Decimal(0))


def test_target_quantity_for_rejects_negative_position_quantity():
    with pytest.raises(ValueError, match="position_quantity"):
        target_quantity_for(PositionTarget.LONG, Decimal(-1))


# --- no-op ---


def test_no_op_flat_to_flat():
    state = _flat_state()
    cost_model = _RecordingCostModel()
    result = _execute(state, PositionTarget.FLAT, cost_model=cost_model)
    assert result is None
    assert cost_model.calls == []


def test_no_op_long_to_long():
    cost_model = _RecordingCostModel()
    result = _execute(_long_state(), PositionTarget.LONG, cost_model=cost_model)
    assert result is None
    assert cost_model.calls == []


def test_no_op_short_to_short():
    cost_model = _RecordingCostModel()
    result = _execute(_short_state(), PositionTarget.SHORT, cost_model=cost_model)
    assert result is None
    assert cost_model.calls == []


# --- next-open price ---


def test_execution_price_is_next_candle_open_not_signal_candle():
    result = _execute(_flat_state(), PositionTarget.LONG)
    assert result.execution_price == Decimal(500)


# --- availability relation ---


def test_wrong_next_candle_gap_is_rejected():
    wrong_next = _next_candle(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        _execute(_flat_state(), PositionTarget.LONG, next_c=wrong_next)


def test_same_bar_execution_is_rejected():
    same_bar_next = _next_candle(_SIGNAL_TIME)
    with pytest.raises(ValueError):
        _execute(_flat_state(), PositionTarget.LONG, next_c=same_bar_next)


def test_non_utc_aware_equivalent_next_candle_is_accepted():
    plus_five = timezone(timedelta(hours=5))
    equivalent_next = _next_candle(
        datetime(2024, 1, 1, 16, 0, tzinfo=plus_five),
        open_price=Decimal(500),  # == 11:00 UTC
    )
    result = _execute(_flat_state(), PositionTarget.LONG, next_c=equivalent_next)
    assert result is not None


# --- metadata consistency ---


def test_symbol_mismatch_is_rejected():
    mismatched_next = _next_candle(symbol="ETHUSDT")
    with pytest.raises(ValueError):
        _execute(_flat_state(), PositionTarget.LONG, next_c=mismatched_next)


def test_timeframe_mismatch_is_rejected():
    mismatched_next = _next_candle(timeframe="4h")
    with pytest.raises(ValueError):
        _execute(_flat_state(), PositionTarget.LONG, next_c=mismatched_next)


# --- all six transitions ---


def test_flat_to_long():
    result = _execute(_flat_state(), PositionTarget.LONG)
    assert result.quantity_delta == Decimal(1)
    assert result.state.cash == Decimal(500)
    assert result.state.position_quantity == Decimal(1)
    assert result.state.average_entry_price == Decimal(500)
    assert result.state.realized_pnl == Decimal(0)


def test_long_to_flat():
    result = _execute(_long_state(), PositionTarget.FLAT)
    assert result.quantity_delta == Decimal(-1)
    assert result.state.cash == Decimal(1400)
    assert result.state.position_quantity == Decimal(0)
    assert result.state.average_entry_price is None
    assert result.state.realized_pnl == Decimal(400)


def test_flat_to_short():
    result = _execute(_flat_state(), PositionTarget.SHORT)
    assert result.quantity_delta == Decimal(-1)
    assert result.state.cash == Decimal(1500)
    assert result.state.position_quantity == Decimal(-1)
    assert result.state.average_entry_price == Decimal(500)
    assert result.state.realized_pnl == Decimal(0)


def test_short_to_flat():
    result = _execute(_short_state(), PositionTarget.FLAT)
    assert result.quantity_delta == Decimal(1)
    assert result.state.cash == Decimal(600)
    assert result.state.position_quantity == Decimal(0)
    assert result.state.average_entry_price is None
    assert result.state.realized_pnl == Decimal(-400)


def test_long_to_short_reversal():
    result = _execute(_long_state(), PositionTarget.SHORT)
    assert result.quantity_delta == Decimal(-2)
    assert result.state.cash == Decimal(1900)
    assert result.state.position_quantity == Decimal(-1)
    assert result.state.average_entry_price == Decimal(500)
    assert result.state.realized_pnl == Decimal(400)


def test_short_to_long_reversal():
    result = _execute(_short_state(), PositionTarget.LONG)
    assert result.quantity_delta == Decimal(2)
    assert result.state.cash == Decimal(100)
    assert result.state.position_quantity == Decimal(1)
    assert result.state.average_entry_price == Decimal(500)
    assert result.state.realized_pnl == Decimal(-400)


# --- reversal quantity passed to CostModel ---


def test_long_to_short_cost_model_receives_absolute_2q():
    cost_model = _RecordingCostModel()
    _execute(_long_state(), PositionTarget.SHORT, cost_model=cost_model)
    assert cost_model.calls[0]["quantity"] == Decimal(2)
    assert cost_model.calls[0]["execution_price"] == Decimal(500)


def test_short_to_long_cost_model_receives_absolute_2q():
    cost_model = _RecordingCostModel()
    _execute(_short_state(), PositionTarget.LONG, cost_model=cost_model)
    assert cost_model.calls[0]["quantity"] == Decimal(2)
    assert cost_model.calls[0]["execution_price"] == Decimal(500)


# --- cost flows into accounting ---


def test_cost_is_deducted_from_cash_and_not_realized_pnl():
    cost_model = _RecordingCostModel(cost=Decimal(2))
    result = _execute(_flat_state(), PositionTarget.LONG, cost_model=cost_model)
    assert result.state.cash == Decimal(498)  # 1000 - 500 - 2
    assert result.state.realized_pnl == Decimal(0)
    assert result.cost == Decimal(2)


def test_zero_cost_model_produces_zero_cost():
    result = _execute(_flat_state(), PositionTarget.LONG, cost_model=ZeroCostModel())
    assert result.cost == Decimal(0)
    assert result.state.cash == Decimal(500)


def test_bad_cost_model_return_type_raises_type_error():
    class _BadCostModel:
        def calculate_cost(self, *, quantity, execution_price):
            return 1.5

    with pytest.raises(TypeError):
        _execute(_flat_state(), PositionTarget.LONG, cost_model=_BadCostModel())


# --- invalid current position ---


def test_current_position_outside_fixed_set_is_rejected():
    invalid_state = AccountState(
        cash=Decimal(800),
        position_quantity=Decimal(2),
        average_entry_price=Decimal(100),
        realized_pnl=Decimal(0),
    )
    with pytest.raises(ValueError):
        _execute(invalid_state, PositionTarget.FLAT)


def test_fractional_current_position_is_rejected():
    invalid_state = AccountState(
        cash=Decimal(800),
        position_quantity=Decimal("0.5"),
        average_entry_price=Decimal(100),
        realized_pnl=Decimal(0),
    )
    with pytest.raises(ValueError):
        _execute(invalid_state, PositionTarget.LONG)


# --- determinism / immutability ---


def test_execution_is_deterministic():
    state = _flat_state()
    first = _execute(state, PositionTarget.LONG)
    second = _execute(state, PositionTarget.LONG)
    assert first == second


def test_input_state_not_mutated():
    state = _flat_state()
    _execute(state, PositionTarget.LONG)
    assert state.cash == Decimal(1000)
    assert state.position_quantity == Decimal(0)


def test_execution_result_is_frozen():
    result = _execute(_flat_state(), PositionTarget.LONG)
    with pytest.raises(FrozenInstanceError):
        result.cost = Decimal(999)


def test_execution_result_has_exactly_four_fields():
    assert set(ExecutionResult.__dataclass_fields__) == {
        "state",
        "quantity_delta",
        "execution_price",
        "cost",
    }


# --- basic input type validation ---


def test_execute_rejects_non_account_state():
    with pytest.raises(TypeError, match="state"):
        execute_target_on_next_candle(
            "not a state",
            target=PositionTarget.LONG,
            config=_CONFIG,
            signal_candle=_signal_candle(),
            next_candle=_next_candle(),
            cost_model=_RecordingCostModel(),
        )


def test_execute_rejects_non_position_target():
    with pytest.raises(TypeError, match="target"):
        execute_target_on_next_candle(
            _flat_state(),
            target="LONG",
            config=_CONFIG,
            signal_candle=_signal_candle(),
            next_candle=_next_candle(),
            cost_model=_RecordingCostModel(),
        )


def test_execute_rejects_non_backtest_config():
    with pytest.raises(TypeError, match="config"):
        execute_target_on_next_candle(
            _flat_state(),
            target=PositionTarget.LONG,
            config="not a config",
            signal_candle=_signal_candle(),
            next_candle=_next_candle(),
            cost_model=_RecordingCostModel(),
        )


def test_execute_rejects_non_candle_signal():
    with pytest.raises(TypeError, match="signal_candle"):
        execute_target_on_next_candle(
            _flat_state(),
            target=PositionTarget.LONG,
            config=_CONFIG,
            signal_candle="not a candle",
            next_candle=_next_candle(),
            cost_model=_RecordingCostModel(),
        )


def test_execute_rejects_non_candle_next():
    with pytest.raises(TypeError, match="next_candle"):
        execute_target_on_next_candle(
            _flat_state(),
            target=PositionTarget.LONG,
            config=_CONFIG,
            signal_candle=_signal_candle(),
            next_candle="not a candle",
            cost_model=_RecordingCostModel(),
        )
