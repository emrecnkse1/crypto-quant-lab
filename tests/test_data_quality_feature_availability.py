from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.data_quality.feature_availability import (
    feature_availability_time,
    is_candle_available_for_features,
)
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


# --- criterion 33 mandatory tests ---


def test_1h_availability_time_is_open_plus_one_hour():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    assert feature_availability_time(candle) == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_1h_candle_unavailable_one_microsecond_before_boundary():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    as_of = datetime(2024, 1, 1, 10, 59, 59, 999999, tzinfo=UTC)

    assert is_candle_available_for_features(candle, as_of) is False


def test_1h_candle_available_exactly_at_boundary():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    as_of = datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)

    assert is_candle_available_for_features(candle, as_of) is True


def test_candle_unavailable_at_its_own_open_time():
    """Locks the contract: OPEN has no early availability exception."""
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    assert is_candle_available_for_features(candle, candle.open_time) is False


# --- additional tests ---


def test_1h_candle_available_one_microsecond_after_boundary():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    as_of = datetime(2024, 1, 1, 11, 0, 0, 1, tzinfo=UTC)

    assert is_candle_available_for_features(candle, as_of) is True


def test_4h_availability_time_is_open_plus_four_hours():
    candle = _make_candle(datetime(2024, 1, 1, 12, 0, tzinfo=UTC), timeframe="4h")

    assert feature_availability_time(candle) == datetime(2024, 1, 1, 16, 0, tzinfo=UTC)


def test_non_utc_aware_as_of_same_instant_gives_correct_result():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    plus_five = timezone(timedelta(hours=5))
    as_of = datetime(2024, 1, 1, 16, 0, 0, tzinfo=plus_five)  # == 11:00:00 UTC

    assert is_candle_available_for_features(candle, as_of) is True


def test_availability_time_output_is_utc_aware():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    result = feature_availability_time(candle)

    assert result.tzinfo is UTC


def test_naive_as_of_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    with pytest.raises(ValueError):
        is_candle_available_for_features(candle, datetime(2024, 1, 1, 11, 0, 0))  # noqa: DTZ001


def test_pseudo_naive_as_of_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    with pytest.raises(ValueError):
        is_candle_available_for_features(
            candle, datetime(2024, 1, 1, 11, 0, 0, tzinfo=_BrokenTzInfo())
        )


def test_unsupported_timeframe_is_rejected():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), timeframe="15m")

    with pytest.raises(ValueError, match="timeframe"):
        feature_availability_time(candle)


def test_unsupported_timeframe_is_rejected_via_availability_check():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), timeframe="15m")

    with pytest.raises(ValueError, match="timeframe"):
        is_candle_available_for_features(candle, datetime(2024, 1, 1, 11, 0, tzinfo=UTC))


def test_repeated_calls_are_deterministic():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    as_of = datetime(2024, 1, 1, 11, 0, tzinfo=UTC)

    first = is_candle_available_for_features(candle, as_of)
    second = is_candle_available_for_features(candle, as_of)

    assert first == second
    assert feature_availability_time(candle) == feature_availability_time(candle)


def test_candle_is_not_mutated():
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))
    original = Candle(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )

    feature_availability_time(candle)
    is_candle_available_for_features(candle, datetime(2024, 1, 1, 11, 0, tzinfo=UTC))

    assert candle == original


# --- anti-lookahead regression ---


def test_availability_is_not_the_open_time_itself():
    """Regression: availability must be open_time + duration, never open_time alone."""
    candle = _make_candle(datetime(2024, 1, 1, 10, 0, tzinfo=UTC))

    assert feature_availability_time(candle) != candle.open_time
    assert is_candle_available_for_features(candle, candle.open_time) is False
