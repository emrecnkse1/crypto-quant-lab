from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import (
    CompositeCostModel,
    ProportionalCommissionModel,
    ProportionalSlippageCostModel,
    ProportionalSpreadCostModel,
    ZeroCostModel,
)


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


# --- ProportionalCommissionModel: calculation ---


def test_commission_standard_calculation():
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("0.200")


def test_commission_return_type_is_genuine_decimal():
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert type(result) is Decimal


def test_commission_zero_rate_returns_zero():
    model = ProportionalCommissionModel(rate=Decimal(0))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_commission_precision_example():
    # Independently hand-computed, not derived via any production helper:
    # 1.2345 * 987.6543 = 1219.25923335
    # 1219.25923335 * 0.0017 = 2.072740696695
    model = ProportionalCommissionModel(rate=Decimal("0.0017"))
    result = model.calculate_cost(quantity=Decimal("1.2345"), execution_price=Decimal("987.6543"))
    assert result == Decimal("2.072740696695")


def test_commission_reversal_sized_quantity():
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(4), execution_price=Decimal(90))
    assert result == Decimal("0.360")


def test_commission_is_deterministic():
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    first = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    second = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert first == second == Decimal("0.200")


# --- ProportionalCommissionModel: CostModel boundary permissiveness ---


@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1)])
def test_commission_permits_zero_and_negative_quantity(quantity):
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=quantity, execution_price=Decimal(100))
    assert result == quantity * Decimal(100) * Decimal("0.001")


@pytest.mark.parametrize("execution_price", [Decimal(0), Decimal(-1)])
def test_commission_permits_zero_and_negative_execution_price(execution_price):
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=execution_price)
    assert result == Decimal(2) * execution_price * Decimal("0.001")


# --- ProportionalCommissionModel: rate construction validation ---


@pytest.mark.parametrize("invalid_rate", [1.0, 1, True, "0.001", None])
def test_commission_rejects_invalid_rate_type(invalid_rate):
    with pytest.raises(TypeError, match="rate"):
        ProportionalCommissionModel(rate=invalid_rate)


@pytest.mark.parametrize(
    "non_finite_rate", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_commission_rejects_non_finite_rate(non_finite_rate):
    with pytest.raises(ValueError, match="finite"):
        ProportionalCommissionModel(rate=non_finite_rate)


def test_commission_rejects_negative_rate():
    with pytest.raises(ValueError, match="rate"):
        ProportionalCommissionModel(rate=Decimal("-0.001"))


@pytest.mark.parametrize("valid_rate", [Decimal(0), Decimal("0.001")])
def test_commission_accepts_zero_and_positive_rate(valid_rate):
    model = ProportionalCommissionModel(rate=valid_rate)
    assert model.rate == valid_rate


# --- ProportionalCommissionModel: calculate_cost input validation ---


@pytest.mark.parametrize("invalid_quantity", [1.0, 1, True, "2", None])
def test_commission_rejects_invalid_quantity_type(invalid_quantity):
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    with pytest.raises(TypeError, match="quantity"):
        model.calculate_cost(quantity=invalid_quantity, execution_price=Decimal(100))


@pytest.mark.parametrize("invalid_execution_price", [100.0, 100, True, "100", None])
def test_commission_rejects_invalid_execution_price_type(invalid_execution_price):
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    with pytest.raises(TypeError, match="execution_price"):
        model.calculate_cost(quantity=Decimal(2), execution_price=invalid_execution_price)


# --- ProportionalCommissionModel: immutability ---


def test_commission_model_is_frozen():
    model = ProportionalCommissionModel(rate=Decimal("0.001"))
    with pytest.raises(FrozenInstanceError):
        model.rate = Decimal("0.002")


# --- ProportionalSpreadCostModel: calculation ---


def test_spread_standard_calculation():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("0.1000")


def test_spread_return_type_is_genuine_decimal():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert type(result) is Decimal


def test_spread_zero_rate_returns_zero():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal(0))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_spread_precision_example():
    # Independently hand-computed, not derived via any production helper:
    # 1.2345 * 987.6543 = 1219.25923335
    # 1219.25923335 * 0.0017 = 2.072740696695
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0017"))
    result = model.calculate_cost(quantity=Decimal("1.2345"), execution_price=Decimal("987.6543"))
    assert result == Decimal("2.072740696695")


def test_spread_reversal_sized_quantity():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    result = model.calculate_cost(quantity=Decimal(4), execution_price=Decimal(90))
    assert result == Decimal("0.18000")


def test_spread_is_deterministic():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    first = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    second = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert first == second == Decimal("0.1000")


# --- ProportionalSpreadCostModel: CostModel boundary permissiveness ---


@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1)])
def test_spread_permits_zero_and_negative_quantity(quantity):
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    result = model.calculate_cost(quantity=quantity, execution_price=Decimal(100))
    assert result == quantity * Decimal(100) * Decimal("0.0005")


@pytest.mark.parametrize("execution_price", [Decimal(0), Decimal(-1)])
def test_spread_permits_zero_and_negative_execution_price(execution_price):
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=execution_price)
    assert result == Decimal(2) * execution_price * Decimal("0.0005")


# --- ProportionalSpreadCostModel: rate construction validation ---


@pytest.mark.parametrize("invalid_rate", [1.0, 1, True, "0.0005", None])
def test_spread_rejects_invalid_rate_type(invalid_rate):
    with pytest.raises(TypeError, match="half_spread_rate"):
        ProportionalSpreadCostModel(half_spread_rate=invalid_rate)


@pytest.mark.parametrize(
    "non_finite_rate", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_spread_rejects_non_finite_rate(non_finite_rate):
    with pytest.raises(ValueError, match="finite"):
        ProportionalSpreadCostModel(half_spread_rate=non_finite_rate)


def test_spread_rejects_negative_rate():
    with pytest.raises(ValueError, match="half_spread_rate"):
        ProportionalSpreadCostModel(half_spread_rate=Decimal("-0.0005"))


@pytest.mark.parametrize("valid_rate", [Decimal(0), Decimal("0.0005")])
def test_spread_accepts_zero_and_positive_rate(valid_rate):
    model = ProportionalSpreadCostModel(half_spread_rate=valid_rate)
    assert model.half_spread_rate == valid_rate


# --- ProportionalSpreadCostModel: calculate_cost input validation ---


@pytest.mark.parametrize("invalid_quantity", [1.0, 1, True, "2", None])
def test_spread_rejects_invalid_quantity_type(invalid_quantity):
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    with pytest.raises(TypeError, match="quantity"):
        model.calculate_cost(quantity=invalid_quantity, execution_price=Decimal(100))


@pytest.mark.parametrize("invalid_execution_price", [100.0, 100, True, "100", None])
def test_spread_rejects_invalid_execution_price_type(invalid_execution_price):
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    with pytest.raises(TypeError, match="execution_price"):
        model.calculate_cost(quantity=Decimal(2), execution_price=invalid_execution_price)


# --- ProportionalSpreadCostModel: immutability ---


def test_spread_model_is_frozen():
    model = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    with pytest.raises(FrozenInstanceError):
        model.half_spread_rate = Decimal("0.001")


# --- ProportionalSlippageCostModel: calculation ---


def test_slippage_standard_calculation():
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("0.200")


def test_slippage_return_type_is_genuine_decimal():
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert type(result) is Decimal


def test_slippage_zero_rate_returns_zero():
    model = ProportionalSlippageCostModel(rate=Decimal(0))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_slippage_precision_example():
    # Independently hand-computed, not derived via any production helper:
    # 1.2345 * 987.6543 = 1219.25923335
    # 1219.25923335 * 0.0017 = 2.072740696695
    model = ProportionalSlippageCostModel(rate=Decimal("0.0017"))
    result = model.calculate_cost(quantity=Decimal("1.2345"), execution_price=Decimal("987.6543"))
    assert result == Decimal("2.072740696695")


def test_slippage_reversal_sized_quantity():
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(4), execution_price=Decimal(90))
    assert result == Decimal("0.360")


def test_slippage_is_deterministic():
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    first = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    second = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert first == second == Decimal("0.200")


# --- ProportionalSlippageCostModel: CostModel boundary permissiveness ---


@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1)])
def test_slippage_permits_zero_and_negative_quantity(quantity):
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=quantity, execution_price=Decimal(100))
    assert result == quantity * Decimal(100) * Decimal("0.001")


@pytest.mark.parametrize("execution_price", [Decimal(0), Decimal(-1)])
def test_slippage_permits_zero_and_negative_execution_price(execution_price):
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=execution_price)
    assert result == Decimal(2) * execution_price * Decimal("0.001")


# --- ProportionalSlippageCostModel: rate construction validation ---


@pytest.mark.parametrize("invalid_rate", [1.0, 1, True, "0.001", None])
def test_slippage_rejects_invalid_rate_type(invalid_rate):
    with pytest.raises(TypeError, match="rate"):
        ProportionalSlippageCostModel(rate=invalid_rate)


@pytest.mark.parametrize(
    "non_finite_rate", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_slippage_rejects_non_finite_rate(non_finite_rate):
    with pytest.raises(ValueError, match="finite"):
        ProportionalSlippageCostModel(rate=non_finite_rate)


def test_slippage_rejects_negative_rate():
    with pytest.raises(ValueError, match="rate"):
        ProportionalSlippageCostModel(rate=Decimal("-0.001"))


@pytest.mark.parametrize("valid_rate", [Decimal(0), Decimal("0.001")])
def test_slippage_accepts_zero_and_positive_rate(valid_rate):
    model = ProportionalSlippageCostModel(rate=valid_rate)
    assert model.rate == valid_rate


# --- ProportionalSlippageCostModel: calculate_cost input validation ---


@pytest.mark.parametrize("invalid_quantity", [1.0, 1, True, "2", None])
def test_slippage_rejects_invalid_quantity_type(invalid_quantity):
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    with pytest.raises(TypeError, match="quantity"):
        model.calculate_cost(quantity=invalid_quantity, execution_price=Decimal(100))


@pytest.mark.parametrize("invalid_execution_price", [100.0, 100, True, "100", None])
def test_slippage_rejects_invalid_execution_price_type(invalid_execution_price):
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    with pytest.raises(TypeError, match="execution_price"):
        model.calculate_cost(quantity=Decimal(2), execution_price=invalid_execution_price)


# --- ProportionalSlippageCostModel: immutability ---


def test_slippage_model_is_frozen():
    model = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    with pytest.raises(FrozenInstanceError):
        model.rate = Decimal("0.002")


# --- CompositeCostModel: test-only doubles ---


class RecordingCostModel:
    """Test-only double: records call order/inputs, returns a fixed Decimal."""

    def __init__(self, label, return_value, call_log, received_log=None):
        self.label = label
        self.return_value = return_value
        self.call_log = call_log
        self.received_log = received_log

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        self.call_log.append(self.label)
        if self.received_log is not None:
            self.received_log.append((quantity, execution_price))
        return self.return_value


class _CompositeTestException(Exception):
    """Test-only marker exception for exception-propagation tests."""


class FailingCostModel:
    """Test-only double: raises a dedicated test exception."""

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        raise _CompositeTestException("boom")


class InvalidReturnCostModel:
    """Test-only double: returns a configurable non-Decimal object."""

    def __init__(self, return_value):
        self.return_value = return_value

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal):
        return self.return_value


# --- CompositeCostModel: worked example (real production models) ---


def test_composite_worked_example_with_real_models():
    commission = ProportionalCommissionModel(rate=Decimal("0.001"))
    spread = ProportionalSpreadCostModel(half_spread_rate=Decimal("0.0005"))
    slippage = ProportionalSlippageCostModel(rate=Decimal("0.001"))
    model = CompositeCostModel(components=(commission, spread, slippage))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("0.5000")


# --- CompositeCostModel: empty composite ---


def test_composite_empty_tuple_returns_zero():
    model = CompositeCostModel(components=())
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal(0)


def test_composite_empty_tuple_result_is_genuine_decimal():
    model = CompositeCostModel(components=())
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert type(result) is Decimal


# --- CompositeCostModel: container validation ---


def _generator_components():
    yield ZeroCostModel()


@pytest.mark.parametrize(
    "invalid_components",
    [[], set(), iter(()), [ZeroCostModel()], _generator_components()],
)
def test_composite_rejects_non_tuple_components(invalid_components):
    with pytest.raises(TypeError, match="components"):
        CompositeCostModel(components=invalid_components)


def test_composite_accepts_genuine_tuple():
    model = CompositeCostModel(components=(ZeroCostModel(),))
    assert isinstance(model.components, tuple)


# --- CompositeCostModel: calculate_cost input validation (even when empty) ---


@pytest.mark.parametrize("invalid_quantity", [1.0, 1, True, "2", None])
def test_composite_rejects_invalid_quantity_type(invalid_quantity):
    model = CompositeCostModel(components=())
    with pytest.raises(TypeError, match="quantity"):
        model.calculate_cost(quantity=invalid_quantity, execution_price=Decimal(100))


@pytest.mark.parametrize("invalid_execution_price", [100.0, 100, True, "100", None])
def test_composite_rejects_invalid_execution_price_type(invalid_execution_price):
    model = CompositeCostModel(components=())
    with pytest.raises(TypeError, match="execution_price"):
        model.calculate_cost(quantity=Decimal(2), execution_price=invalid_execution_price)


# --- CompositeCostModel: component output validation ---


@pytest.mark.parametrize("invalid_output", [1.0, 1, True, "0.1", None])
def test_composite_rejects_non_decimal_component_output(invalid_output):
    model = CompositeCostModel(components=(InvalidReturnCostModel(invalid_output),))
    with pytest.raises(TypeError, match=r"components\[0\]"):
        model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))


# --- CompositeCostModel: negative component output / total ---


def test_composite_single_negative_component_output():
    call_log = []
    model = CompositeCostModel(components=(RecordingCostModel("A", Decimal("-0.25"), call_log),))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("-0.25")


def test_composite_negative_total_from_mixed_components():
    call_log = []
    model = CompositeCostModel(
        components=(
            RecordingCostModel("A", Decimal("0.10"), call_log),
            RecordingCostModel("B", Decimal("-0.50"), call_log),
        )
    )
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("-0.40")


# --- CompositeCostModel: exact left-to-right order + exactly-once ---


def test_composite_invokes_components_in_exact_order_exactly_once():
    call_log = []
    model = CompositeCostModel(
        components=(
            RecordingCostModel("A", Decimal("0.10"), call_log),
            RecordingCostModel("B", Decimal("0.20"), call_log),
            RecordingCostModel("C", Decimal("0.30"), call_log),
        )
    )
    model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert call_log == ["A", "B", "C"]


# --- CompositeCostModel: input forwarding ---


def test_composite_forwards_exact_inputs_unchanged():
    received = []
    call_log = []
    quantity = Decimal("1.2345")
    execution_price = Decimal("987.6543")
    model = CompositeCostModel(
        components=(
            RecordingCostModel("A", Decimal(0), call_log, received_log=received),
            RecordingCostModel("B", Decimal(0), call_log, received_log=received),
        )
    )
    model.calculate_cost(quantity=quantity, execution_price=execution_price)
    assert received == [(quantity, execution_price), (quantity, execution_price)]


# --- CompositeCostModel: exception propagation + short-circuit ---


def test_composite_propagates_component_exception_and_short_circuits():
    call_log = []
    model = CompositeCostModel(
        components=(
            RecordingCostModel("A", Decimal("0.10"), call_log),
            FailingCostModel(),
            RecordingCostModel("C", Decimal("0.30"), call_log),
        )
    )
    with pytest.raises(_CompositeTestException):
        model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert call_log == ["A"]


# --- CompositeCostModel: duplicate component ---


def test_composite_duplicate_component_invoked_twice():
    call_log = []
    component = RecordingCostModel("A", Decimal("0.25"), call_log)
    model = CompositeCostModel(components=(component, component))
    result = model.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert call_log == ["A", "A"]
    assert result == Decimal("0.50")


# --- CompositeCostModel: nested composite ---


def test_composite_nested_composite_sums_correctly():
    call_log = []
    inner = CompositeCostModel(
        components=(
            RecordingCostModel("inner_a", Decimal("0.10"), call_log),
            RecordingCostModel("inner_b", Decimal("0.20"), call_log),
        )
    )
    outer = CompositeCostModel(
        components=(inner, RecordingCostModel("outer_c", Decimal("0.30"), call_log))
    )
    result = outer.calculate_cost(quantity=Decimal(2), execution_price=Decimal(100))
    assert result == Decimal("0.60")


# --- CompositeCostModel: immutability ---


def test_composite_model_is_frozen():
    model = CompositeCostModel(components=())
    with pytest.raises(FrozenInstanceError):
        model.components = (ZeroCostModel(),)
