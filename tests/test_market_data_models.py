from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.models import Candle


def make_candle(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "open_time": datetime(2026, 1, 1, tzinfo=UTC),
        "open": Decimal(50000),
        "high": Decimal(50500),
        "low": Decimal(49500),
        "close": Decimal(50200),
        "volume": Decimal("12.5"),
    }
    fields.update(overrides)
    return Candle(**fields)


def test_valid_btcusdt_candle_can_be_created():
    candle = make_candle()
    assert candle.symbol == "BTCUSDT"
    assert candle.close == Decimal(50200)


def test_candle_is_immutable():
    candle = make_candle()
    with pytest.raises(FrozenInstanceError):
        candle.close = Decimal(1)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        make_candle(open_time=datetime(2026, 1, 1))  # noqa: DTZ001


def test_negative_volume_is_rejected():
    with pytest.raises(ValueError, match="volume"):
        make_candle(volume=Decimal(-1))


def test_invalid_high_is_rejected():
    with pytest.raises(ValueError, match="high"):
        make_candle(high=Decimal(49000))


def test_invalid_low_is_rejected():
    with pytest.raises(ValueError, match="low"):
        make_candle(low=Decimal(51000))


def test_empty_symbol_is_rejected():
    with pytest.raises(ValueError, match="symbol"):
        make_candle(symbol="")


def test_empty_timeframe_is_rejected():
    with pytest.raises(ValueError, match="timeframe"):
        make_candle(timeframe="")
