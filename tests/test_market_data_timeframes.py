from datetime import timedelta

import pytest

from crypto_quant_lab.market_data.timeframes import candle_duration


def test_1h_duration_is_one_hour():
    assert candle_duration("1h") == timedelta(hours=1)


def test_4h_duration_is_four_hours():
    assert candle_duration("4h") == timedelta(hours=4)


def test_unsupported_normal_timeframe_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("15m")


def test_calendar_variable_timeframe_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("1M")


def test_empty_string_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("")


def test_uppercase_case_variant_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("1H")


def test_uppercase_4h_case_variant_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("4H")


def test_leading_whitespace_variant_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration(" 1h")


def test_trailing_whitespace_variant_is_rejected():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration("1h ")


@pytest.mark.parametrize(
    "timeframe",
    ["1d", "3d", "1w", "1m", "5m", "30m", "2h", "8h", "12h", "1s"],
)
def test_other_unsupported_timeframes_are_rejected(timeframe):
    with pytest.raises(ValueError, match="unsupported timeframe"):
        candle_duration(timeframe)
