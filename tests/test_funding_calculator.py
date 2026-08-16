from decimal import Decimal

import pytest

from crypto_quant_lab.funding.calculator import LinearFundingModel

MODEL = LinearFundingModel()


# --- sign matrix ---


def test_long_positive_rate_pays():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(2),
        reference_price=Decimal(100),
        funding_rate=Decimal("0.001"),
    )
    assert cost == Decimal("0.200")


def test_short_positive_rate_receives():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(-2),
        reference_price=Decimal(100),
        funding_rate=Decimal("0.001"),
    )
    assert cost == Decimal("-0.200")


def test_long_negative_rate_receives():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(2),
        reference_price=Decimal(100),
        funding_rate=Decimal("-0.001"),
    )
    assert cost == Decimal("-0.200")


def test_short_negative_rate_pays():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(-2),
        reference_price=Decimal(100),
        funding_rate=Decimal("-0.001"),
    )
    assert cost == Decimal("0.200")


# --- zero cases ---


def test_zero_position_yields_zero():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(0),
        reference_price=Decimal(100),
        funding_rate=Decimal("0.001"),
    )
    assert cost == Decimal(0)


def test_zero_rate_yields_zero():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(2),
        reference_price=Decimal(100),
        funding_rate=Decimal(0),
    )
    assert cost == Decimal(0)


# --- precision / determinism ---


def test_high_precision_decimal_exactness():
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal("3.123456789"),
        reference_price=Decimal("65432.123456789"),
        funding_rate=Decimal("0.000123456789"),
    )
    expected = Decimal("3.123456789") * Decimal("65432.123456789") * Decimal("0.000123456789")
    assert cost == expected


def test_deterministic_repeated_call():
    kwargs = {
        "signed_position_quantity": Decimal(2),
        "reference_price": Decimal(100),
        "funding_rate": Decimal("0.001"),
    }
    first = MODEL.calculate_funding_cost(**kwargs)
    second = MODEL.calculate_funding_cost(**kwargs)
    assert first == second


# --- type validation ---


@pytest.mark.parametrize("bad_value", [2, 2.0, True, "2"])
def test_signed_position_quantity_type_is_rejected(bad_value):
    with pytest.raises(TypeError, match="signed_position_quantity"):
        MODEL.calculate_funding_cost(
            signed_position_quantity=bad_value,
            reference_price=Decimal(100),
            funding_rate=Decimal("0.001"),
        )


@pytest.mark.parametrize("bad_value", [100, 100.0, True, "100"])
def test_reference_price_type_is_rejected(bad_value):
    with pytest.raises(TypeError, match="reference_price"):
        MODEL.calculate_funding_cost(
            signed_position_quantity=Decimal(2),
            reference_price=bad_value,
            funding_rate=Decimal("0.001"),
        )


@pytest.mark.parametrize("bad_value", [0, 0.001, True, "0.001"])
def test_funding_rate_type_is_rejected(bad_value):
    with pytest.raises(TypeError, match="funding_rate"):
        MODEL.calculate_funding_cost(
            signed_position_quantity=Decimal(2),
            reference_price=Decimal(100),
            funding_rate=bad_value,
        )


# --- reference_price value validation ---


@pytest.mark.parametrize(
    "bad_price",
    [
        Decimal(0),
        Decimal(-1),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_reference_price_invalid_values_are_rejected(bad_price):
    with pytest.raises(ValueError, match="reference_price"):
        MODEL.calculate_funding_cost(
            signed_position_quantity=Decimal(2),
            reference_price=bad_price,
            funding_rate=Decimal("0.001"),
        )


# --- funding_rate value validation ---


@pytest.mark.parametrize(
    "bad_rate", [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_funding_rate_non_finite_is_rejected(bad_rate):
    with pytest.raises(ValueError, match="funding_rate"):
        MODEL.calculate_funding_cost(
            signed_position_quantity=Decimal(2),
            reference_price=Decimal(100),
            funding_rate=bad_rate,
        )


@pytest.mark.parametrize("rate", [Decimal("-0.001"), Decimal(0), Decimal("0.001")])
def test_funding_rate_finite_signs_are_legal(rate):
    cost = MODEL.calculate_funding_cost(
        signed_position_quantity=Decimal(2), reference_price=Decimal(100), funding_rate=rate
    )
    assert cost == Decimal(2) * Decimal(100) * rate
