from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel


def test_valid_decimal_inputs_return_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_return_value_is_genuine_decimal():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(100))
    assert type(result) is Decimal


def test_reversal_sized_quantity_returns_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_zero_quantity_returns_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(0), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_negative_quantity_returns_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(-1), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_zero_execution_price_returns_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(0))
    assert result == Decimal(0)


def test_negative_execution_price_returns_zero():
    model = ZeroCostModel()
    result = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(-1))
    assert result == Decimal(0)


def test_repeated_calls_are_deterministic():
    model = ZeroCostModel()
    first = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(100))
    second = model.calculate_cost(quantity=Decimal(1), execution_price=Decimal(100))
    assert first == second == Decimal(0)


def test_two_independent_instances_behave_identically():
    first_model = ZeroCostModel()
    second_model = ZeroCostModel()
    first = first_model.calculate_cost(quantity=Decimal(3), execution_price=Decimal(50))
    second = second_model.calculate_cost(quantity=Decimal(3), execution_price=Decimal(50))
    assert first == second == Decimal(0)


@pytest.mark.parametrize("invalid_quantity", [1.0, 1, True])
def test_invalid_quantity_type_is_rejected(invalid_quantity):
    model = ZeroCostModel()
    with pytest.raises(TypeError, match="quantity"):
        model.calculate_cost(quantity=invalid_quantity, execution_price=Decimal(100))


@pytest.mark.parametrize("invalid_execution_price", [100.0, 100, True])
def test_invalid_execution_price_type_is_rejected(invalid_execution_price):
    model = ZeroCostModel()
    with pytest.raises(TypeError, match="execution_price"):
        model.calculate_cost(quantity=Decimal(1), execution_price=invalid_execution_price)
