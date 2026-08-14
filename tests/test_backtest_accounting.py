from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.accounting import AccountState, apply_fill, equity, unrealized_pnl


def _flat_state(cash=Decimal(1000)):
    return AccountState(
        cash=cash, position_quantity=Decimal(0), average_entry_price=None, realized_pnl=Decimal(0)
    )


def _long_state(cash=Decimal(900), quantity=Decimal(1), entry=Decimal(100)):
    return AccountState(
        cash=cash, position_quantity=quantity, average_entry_price=entry, realized_pnl=Decimal(0)
    )


def _short_state(cash=Decimal(1100), quantity=Decimal(-1), entry=Decimal(100)):
    return AccountState(
        cash=cash, position_quantity=quantity, average_entry_price=entry, realized_pnl=Decimal(0)
    )


# --- AccountState: shape / invariants ---


def test_valid_flat_state():
    state = _flat_state()
    assert state.position_quantity == Decimal(0)
    assert state.average_entry_price is None


def test_valid_long_state():
    state = _long_state()
    assert state.position_quantity == Decimal(1)
    assert state.average_entry_price == Decimal(100)


def test_valid_short_state():
    state = _short_state()
    assert state.position_quantity == Decimal(-1)
    assert state.average_entry_price == Decimal(100)


def test_flat_with_non_none_average_entry_price_is_rejected():
    with pytest.raises(ValueError, match="average_entry_price"):
        AccountState(
            cash=Decimal(1000),
            position_quantity=Decimal(0),
            average_entry_price=Decimal(100),
            realized_pnl=Decimal(0),
        )


@pytest.mark.parametrize("quantity", [Decimal(1), Decimal(-1)])
def test_non_flat_with_none_average_entry_price_is_rejected(quantity):
    with pytest.raises(ValueError, match="average_entry_price"):
        AccountState(
            cash=Decimal(1000),
            position_quantity=quantity,
            average_entry_price=None,
            realized_pnl=Decimal(0),
        )


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("cash", 1000.0),
        ("position_quantity", 0.0),
        ("position_quantity", True),
        ("realized_pnl", 0.0),
    ],
)
def test_invalid_financial_type_is_rejected(field_name, invalid_value):
    kwargs = {
        "cash": Decimal(1000),
        "position_quantity": Decimal(0),
        "average_entry_price": None,
        "realized_pnl": Decimal(0),
    }
    kwargs[field_name] = invalid_value
    with pytest.raises(TypeError, match=field_name):
        AccountState(**kwargs)


@pytest.mark.parametrize("invalid_avg", [100.0, True])
def test_invalid_average_entry_price_type_is_rejected(invalid_avg):
    with pytest.raises(TypeError, match="average_entry_price"):
        AccountState(
            cash=Decimal(1000),
            position_quantity=Decimal(1),
            average_entry_price=invalid_avg,
            realized_pnl=Decimal(0),
        )


def test_state_is_frozen():
    state = _flat_state()
    with pytest.raises(FrozenInstanceError):
        state.cash = Decimal(0)


def test_state_has_no_instance_dict():
    state = _flat_state()
    assert not hasattr(state, "__dict__")


# --- apply_fill: opening from flat ---


def test_flat_to_long_transition():
    new_state = apply_fill(
        _flat_state(), quantity_delta=Decimal(1), execution_price=Decimal(100), fill_cost=Decimal(0)
    )
    assert new_state.cash == Decimal(900)
    assert new_state.position_quantity == Decimal(1)
    assert new_state.average_entry_price == Decimal(100)
    assert new_state.realized_pnl == Decimal(0)


def test_flat_to_short_transition():
    new_state = apply_fill(
        _flat_state(),
        quantity_delta=Decimal(-1),
        execution_price=Decimal(100),
        fill_cost=Decimal(0),
    )
    assert new_state.cash == Decimal(1100)
    assert new_state.position_quantity == Decimal(-1)
    assert new_state.average_entry_price == Decimal(100)
    assert new_state.realized_pnl == Decimal(0)


# --- apply_fill: full close ---


def test_long_close_profit():
    new_state = apply_fill(
        _long_state(),
        quantity_delta=Decimal(-1),
        execution_price=Decimal(110),
        fill_cost=Decimal(0),
    )
    assert new_state.realized_pnl == Decimal(10)
    assert new_state.position_quantity == Decimal(0)
    assert new_state.average_entry_price is None
    assert new_state.cash == Decimal(1010)


def test_long_close_loss():
    new_state = apply_fill(
        _long_state(), quantity_delta=Decimal(-1), execution_price=Decimal(90), fill_cost=Decimal(0)
    )
    assert new_state.realized_pnl == Decimal(-10)
    assert new_state.cash == Decimal(990)


def test_short_close_profit():
    new_state = apply_fill(
        _short_state(), quantity_delta=Decimal(1), execution_price=Decimal(90), fill_cost=Decimal(0)
    )
    assert new_state.realized_pnl == Decimal(10)
    assert new_state.position_quantity == Decimal(0)
    assert new_state.average_entry_price is None
    assert new_state.cash == Decimal(1010)


def test_short_close_loss():
    new_state = apply_fill(
        _short_state(),
        quantity_delta=Decimal(1),
        execution_price=Decimal(110),
        fill_cost=Decimal(0),
    )
    assert new_state.realized_pnl == Decimal(-10)
    assert new_state.cash == Decimal(990)


# --- apply_fill: reversal ---


def test_long_to_short_reversal():
    new_state = apply_fill(
        _long_state(), quantity_delta=Decimal(-2), execution_price=Decimal(90), fill_cost=Decimal(0)
    )
    assert new_state.position_quantity == Decimal(-1)
    assert new_state.average_entry_price == Decimal(90)
    assert new_state.realized_pnl == Decimal(-10)
    assert new_state.cash == Decimal(1080)


def test_short_to_long_reversal():
    new_state = apply_fill(
        _short_state(), quantity_delta=Decimal(2), execution_price=Decimal(90), fill_cost=Decimal(0)
    )
    assert new_state.position_quantity == Decimal(1)
    assert new_state.average_entry_price == Decimal(90)
    assert new_state.realized_pnl == Decimal(10)
    assert new_state.cash == Decimal(920)


# --- apply_fill: illegal transitions ---


def test_same_direction_long_add_is_rejected():
    with pytest.raises(ValueError):
        apply_fill(
            _long_state(),
            quantity_delta=Decimal(1),
            execution_price=Decimal(100),
            fill_cost=Decimal(0),
        )


def test_same_direction_short_add_is_rejected():
    with pytest.raises(ValueError):
        apply_fill(
            _short_state(),
            quantity_delta=Decimal(-1),
            execution_price=Decimal(100),
            fill_cost=Decimal(0),
        )


def test_long_partial_close_is_rejected():
    state = _long_state(cash=Decimal(800), quantity=Decimal(2))
    with pytest.raises(ValueError):
        apply_fill(
            state, quantity_delta=Decimal(-1), execution_price=Decimal(100), fill_cost=Decimal(0)
        )


def test_short_partial_close_is_rejected():
    state = _short_state(cash=Decimal(1200), quantity=Decimal(-2))
    with pytest.raises(ValueError):
        apply_fill(
            state, quantity_delta=Decimal(1), execution_price=Decimal(100), fill_cost=Decimal(0)
        )


def test_zero_quantity_delta_is_rejected():
    with pytest.raises(ValueError):
        apply_fill(
            _flat_state(),
            quantity_delta=Decimal(0),
            execution_price=Decimal(100),
            fill_cost=Decimal(0),
        )


# --- apply_fill: cost ---


def test_fill_cost_is_deducted_from_cash_and_does_not_affect_realized_pnl():
    new_state = apply_fill(
        _flat_state(),
        quantity_delta=Decimal(1),
        execution_price=Decimal(100),
        fill_cost=Decimal(2),
    )
    assert new_state.cash == Decimal(898)
    assert new_state.realized_pnl == Decimal(0)


def test_negative_fill_cost_rebate_increases_cash():
    new_state = apply_fill(
        _flat_state(),
        quantity_delta=Decimal(1),
        execution_price=Decimal(100),
        fill_cost=Decimal(-2),
    )
    assert new_state.cash == Decimal(902)


# --- apply_fill: immutability / determinism ---


def test_apply_fill_does_not_mutate_input_state():
    state = _flat_state()
    apply_fill(state, quantity_delta=Decimal(1), execution_price=Decimal(100), fill_cost=Decimal(0))
    assert state.cash == Decimal(1000)
    assert state.position_quantity == Decimal(0)


def test_apply_fill_is_deterministic():
    state = _flat_state()
    first = apply_fill(
        state, quantity_delta=Decimal(1), execution_price=Decimal(100), fill_cost=Decimal(0)
    )
    second = apply_fill(
        state, quantity_delta=Decimal(1), execution_price=Decimal(100), fill_cost=Decimal(0)
    )
    assert first == second


# --- apply_fill: type validation ---


@pytest.mark.parametrize("invalid_value", [1.0, 1, True])
def test_apply_fill_rejects_invalid_quantity_delta_type(invalid_value):
    with pytest.raises(TypeError, match="quantity_delta"):
        apply_fill(
            _flat_state(),
            quantity_delta=invalid_value,
            execution_price=Decimal(100),
            fill_cost=Decimal(0),
        )


@pytest.mark.parametrize("invalid_value", [100.0, 100, True])
def test_apply_fill_rejects_invalid_execution_price_type(invalid_value):
    with pytest.raises(TypeError, match="execution_price"):
        apply_fill(
            _flat_state(),
            quantity_delta=Decimal(1),
            execution_price=invalid_value,
            fill_cost=Decimal(0),
        )


@pytest.mark.parametrize("invalid_value", [1.0, 1, True])
def test_apply_fill_rejects_invalid_fill_cost_type(invalid_value):
    with pytest.raises(TypeError, match="fill_cost"):
        apply_fill(
            _flat_state(),
            quantity_delta=Decimal(1),
            execution_price=Decimal(100),
            fill_cost=invalid_value,
        )


# --- unrealized_pnl ---


def test_unrealized_pnl_flat_is_zero():
    assert unrealized_pnl(_flat_state(), mark_price=Decimal(100)) == Decimal(0)


def test_unrealized_pnl_long_profit():
    assert unrealized_pnl(_long_state(), mark_price=Decimal(110)) == Decimal(10)


def test_unrealized_pnl_long_loss():
    assert unrealized_pnl(_long_state(), mark_price=Decimal(90)) == Decimal(-10)


def test_unrealized_pnl_short_profit():
    assert unrealized_pnl(_short_state(), mark_price=Decimal(90)) == Decimal(10)


def test_unrealized_pnl_short_loss():
    assert unrealized_pnl(_short_state(), mark_price=Decimal(110)) == Decimal(-10)


@pytest.mark.parametrize("invalid_value", [100.0, 100, True])
def test_unrealized_pnl_rejects_invalid_mark_price_type(invalid_value):
    with pytest.raises(TypeError, match="mark_price"):
        unrealized_pnl(_flat_state(), mark_price=invalid_value)


# --- equity ---


def test_equity_flat_equals_cash():
    assert equity(_flat_state(), mark_price=Decimal(100)) == Decimal(1000)


def test_equity_long_exact():
    assert equity(_long_state(), mark_price=Decimal(110)) == Decimal(1010)


def test_equity_short_exact():
    assert equity(_short_state(), mark_price=Decimal(90)) == Decimal(1010)


@pytest.mark.parametrize("invalid_value", [100.0, 100, True])
def test_equity_rejects_invalid_mark_price_type(invalid_value):
    with pytest.raises(TypeError, match="mark_price"):
        equity(_flat_state(), mark_price=invalid_value)
