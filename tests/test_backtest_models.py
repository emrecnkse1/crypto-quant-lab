from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def _valid_result_kwargs(**overrides):
    kwargs = {
        "initial_cash": Decimal(10000),
        "final_cash": Decimal(10500),
        "final_equity": Decimal(10500),
        "total_realized_pnl": Decimal(500),
        "total_unrealized_pnl": Decimal(0),
        "total_pnl": Decimal(500),
        "total_cost": Decimal(0),
        "fill_count": 2,
        "trade_count": 1,
        "equity_curve": (
            EquityPoint(time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), equity=Decimal(10000)),
            EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(10500)),
        ),
    }
    kwargs.update(overrides)
    return kwargs


# --- PositionTarget ---


def test_position_target_has_exactly_three_states():
    assert {member.value for member in PositionTarget} == {"LONG", "FLAT", "SHORT"}
    assert len(PositionTarget) == 3


def test_position_target_values_are_deterministic():
    assert PositionTarget.LONG is PositionTarget.LONG
    assert PositionTarget.LONG != PositionTarget.SHORT
    assert PositionTarget.LONG != PositionTarget.FLAT


# --- BacktestConfig ---


def test_valid_config_preserves_values_exactly():
    config = BacktestConfig(initial_cash=Decimal(10000), position_quantity=Decimal(1))
    assert config.initial_cash == Decimal(10000)
    assert config.position_quantity == Decimal(1)


def test_config_is_frozen():
    config = BacktestConfig(initial_cash=Decimal(10000), position_quantity=Decimal(1))
    with pytest.raises(FrozenInstanceError):
        config.initial_cash = Decimal(0)


def test_config_zero_position_quantity_is_rejected():
    with pytest.raises(ValueError, match="position_quantity"):
        BacktestConfig(initial_cash=Decimal(10000), position_quantity=Decimal(0))


def test_config_negative_position_quantity_is_rejected():
    with pytest.raises(ValueError, match="position_quantity"):
        BacktestConfig(initial_cash=Decimal(10000), position_quantity=Decimal(-1))


def test_config_float_initial_cash_is_rejected():
    with pytest.raises(TypeError, match="initial_cash"):
        BacktestConfig(initial_cash=10000.0, position_quantity=Decimal(1))


def test_config_float_position_quantity_is_rejected():
    with pytest.raises(TypeError, match="position_quantity"):
        BacktestConfig(initial_cash=Decimal(10000), position_quantity=1.0)


@pytest.mark.parametrize("field_name", ["initial_cash", "position_quantity"])
def test_config_bool_is_rejected(field_name):
    kwargs = {"initial_cash": Decimal(10000), "position_quantity": Decimal(1)}
    kwargs[field_name] = True
    with pytest.raises(TypeError, match=field_name):
        BacktestConfig(**kwargs)


# --- EquityPoint ---


def test_equity_point_preserves_values():
    point = EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(10500))
    assert point.time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    assert point.equity == Decimal(10500)


def test_equity_point_accepts_non_utc_aware_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    point = EquityPoint(time=datetime(2024, 1, 1, 16, 0, tzinfo=plus_five), equity=Decimal(100))
    assert point.time == datetime(2024, 1, 1, 16, 0, tzinfo=plus_five)


def test_equity_point_rejects_naive_datetime():
    with pytest.raises(ValueError):
        EquityPoint(time=datetime(2024, 1, 1, 11, 0), equity=Decimal(100))  # noqa: DTZ001


def test_equity_point_rejects_pseudo_naive_datetime():
    with pytest.raises(ValueError):
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=_BrokenTzInfo()), equity=Decimal(100))


def test_equity_point_rejects_float_equity():
    with pytest.raises(TypeError, match="equity"):
        EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=100.0)


def test_equity_point_is_frozen():
    point = EquityPoint(time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), equity=Decimal(100))
    with pytest.raises(FrozenInstanceError):
        point.equity = Decimal(0)


# --- BacktestResult ---


def test_result_fields_preserved_exactly():
    kwargs = _valid_result_kwargs()
    result = BacktestResult(**kwargs)
    for field_name, value in kwargs.items():
        assert getattr(result, field_name) == value


def test_result_list_equity_curve_is_rejected():
    with pytest.raises(TypeError, match="equity_curve"):
        BacktestResult(
            **_valid_result_kwargs(
                equity_curve=[
                    EquityPoint(
                        time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), equity=Decimal(10000)
                    ),
                ]
            )
        )


def test_result_negative_fill_count_is_rejected():
    with pytest.raises(ValueError, match="fill_count"):
        BacktestResult(**_valid_result_kwargs(fill_count=-1))


def test_result_negative_trade_count_is_rejected():
    with pytest.raises(ValueError, match="trade_count"):
        BacktestResult(**_valid_result_kwargs(trade_count=-1))


@pytest.mark.parametrize("field_name", ["fill_count", "trade_count"])
def test_result_bool_count_is_rejected(field_name):
    with pytest.raises(TypeError, match=field_name):
        BacktestResult(**_valid_result_kwargs(**{field_name: True}))


def test_result_float_financial_field_is_rejected():
    with pytest.raises(TypeError, match="final_equity"):
        BacktestResult(**_valid_result_kwargs(final_equity=10500.0))


def test_result_is_frozen():
    result = BacktestResult(**_valid_result_kwargs())
    with pytest.raises(FrozenInstanceError):
        result.fill_count = 99


def test_result_construction_is_deterministic():
    kwargs = _valid_result_kwargs()
    first = BacktestResult(**kwargs)
    second = BacktestResult(**kwargs)
    assert first == second
