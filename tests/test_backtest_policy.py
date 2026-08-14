from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.models import PositionTarget
from crypto_quant_lab.backtest.policy import PolicyContext
from crypto_quant_lab.market_data.models import Candle


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def _make_candle(open_time: datetime, timeframe: str = "1h") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=open_time,
        open=Decimal(100),
        high=Decimal(100),
        low=Decimal(100),
        close=Decimal(100),
        volume=Decimal(1),
    )


class _FixedPolicy:
    def __init__(self, target: PositionTarget):
        self._target = target

    def target_position(self, context: PolicyContext) -> PositionTarget:
        return self._target


# --- PolicyContext: shape / immutability ---


def test_valid_context_is_accepted():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=(candle,))
    assert context.as_of_time == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_fields_preserved_exactly():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    as_of = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    context = PolicyContext(as_of_time=as_of, candles=(candle,))
    assert context.as_of_time == as_of
    assert context.candles == (candle,)


def test_list_candles_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(TypeError, match="candles"):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=[candle])


def test_empty_candles_tuple_is_accepted():
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=())
    assert context.candles == ()


def test_context_is_frozen():
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=())
    with pytest.raises(FrozenInstanceError):
        context.as_of_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_non_candle_element_is_rejected():
    with pytest.raises(TypeError, match="candles"):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=("not a candle",))


# --- PolicyContext: as_of_time datetime contract ---


def test_naive_as_of_time_is_rejected():
    with pytest.raises(ValueError):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0), candles=())  # noqa: DTZ001


def test_pseudo_naive_as_of_time_is_rejected():
    with pytest.raises(ValueError):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=_BrokenTzInfo()), candles=())


def test_non_utc_aware_equivalent_as_of_time_is_accepted():
    plus_five = timezone(timedelta(hours=5))
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    context = PolicyContext(
        as_of_time=datetime(2024, 1, 1, 16, 0, tzinfo=plus_five),  # == 11:00 UTC
        candles=(candle,),
    )
    assert context.candles == (candle,)


# --- PolicyContext: anti-lookahead boundary ---


def test_exact_availability_boundary_is_accepted():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=(candle,))
    assert context.candles == (candle,)


def test_one_microsecond_before_availability_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        PolicyContext(
            as_of_time=datetime(2024, 1, 1, 10, 59, 59, 999999, tzinfo=UTC), candles=(candle,)
        )


def test_one_microsecond_after_availability_is_accepted():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    context = PolicyContext(
        as_of_time=datetime(2024, 1, 1, 11, 0, 0, 1, tzinfo=UTC), candles=(candle,)
    )
    assert context.candles == (candle,)


def test_multiple_available_candles_are_accepted():
    candles = (
        _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_candle(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),
    )
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), candles=candles)
    assert context.candles == candles


def test_future_candle_anywhere_in_tuple_is_rejected():
    candles = (
        _make_candle(datetime(2024, 1, 1, 9, 0, tzinfo=UTC)),
        _make_candle(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)),  # availability 12:00, not yet
    )
    with pytest.raises(ValueError):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=candles)


# --- PolicyContext: ordering defense-in-depth ---


def test_duplicate_open_time_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), candles=(candle, candle))


def test_backward_open_time_is_rejected():
    candles = (
        _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC)),
        _make_candle(datetime(2024, 1, 1, 9, 0, tzinfo=UTC)),
    )
    with pytest.raises(ValueError):
        PolicyContext(as_of_time=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), candles=candles)


# --- BacktestPolicy Protocol behavior ---


def test_always_long_duck_typed_policy_returns_long():
    class _AlwaysLongPolicy:
        def target_position(self, context: PolicyContext) -> PositionTarget:
            return PositionTarget.LONG

    policy = _AlwaysLongPolicy()
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=())

    assert policy.target_position(context) is PositionTarget.LONG


@pytest.mark.parametrize("target", [PositionTarget.FLAT, PositionTarget.SHORT])
def test_fixed_policy_returns_configured_target(target):
    policy = _FixedPolicy(target)
    context = PolicyContext(as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), candles=())

    assert policy.target_position(context) is target
