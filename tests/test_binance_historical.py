import copy
from datetime import UTC, datetime

import pytest

from crypto_quant_lab.market_data.binance_historical import (
    BinanceHistoricalKline,
    parse_binance_historical_kline,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.market_data.parsers import parse_binance_kline

VALID_KLINE = [
    1704067200000,
    "50000.00000000",
    "50500.00000000",
    "49500.00000000",
    "50200.00000000",
    "12.50000000",
    1704070799999,
    "625000.00",
    100,
    "6.00000000",
    "300000.00",
    "0",
]


def test_valid_raw_kline_returns_binance_historical_kline():
    result = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert isinstance(result, BinanceHistoricalKline)


def test_envelope_candle_matches_parse_binance_kline():
    result = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    expected_candle = parse_binance_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert result.candle == expected_candle
    assert isinstance(result.candle, Candle)


def test_close_time_converts_raw_millisecond_to_utc_datetime():
    result = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert result.close_time == datetime(2024, 1, 1, 0, 59, 59, 999000, tzinfo=UTC)


def test_close_time_is_timezone_aware_utc():
    result = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert result.close_time.tzinfo is not None
    assert result.close_time.utcoffset().total_seconds() == 0


def test_extra_trailing_binance_fields_do_not_break_parsing():
    raw_with_extra = [*VALID_KLINE, "extra-unused-field"]

    result = parse_binance_historical_kline(raw_with_extra, "BTCUSDT", "1h")

    assert isinstance(result, BinanceHistoricalKline)
    assert result.close_time == datetime(2024, 1, 1, 0, 59, 59, 999000, tzinfo=UTC)


def test_missing_close_time_field_is_rejected():
    truncated = VALID_KLINE[:6]

    with pytest.raises(ValueError, match="raw kline"):
        parse_binance_historical_kline(truncated, "BTCUSDT", "1h")


def test_close_time_bool_is_rejected():
    raw = list(VALID_KLINE)
    raw[6] = True

    with pytest.raises(ValueError, match="close_time"):
        parse_binance_historical_kline(raw, "BTCUSDT", "1h")


def test_close_time_float_is_rejected():
    raw = list(VALID_KLINE)
    raw[6] = 1704070799999.0

    with pytest.raises(ValueError, match="close_time"):
        parse_binance_historical_kline(raw, "BTCUSDT", "1h")


def test_close_time_string_is_rejected():
    raw = list(VALID_KLINE)
    raw[6] = "1704070799999"

    with pytest.raises(ValueError, match="close_time"):
        parse_binance_historical_kline(raw, "BTCUSDT", "1h")


def test_close_time_out_of_datetime_range_is_rejected():
    raw = list(VALID_KLINE)
    raw[6] = 10**30

    with pytest.raises(ValueError, match="close_time"):
        parse_binance_historical_kline(raw, "BTCUSDT", "1h")


def test_existing_candle_parsing_semantics_are_preserved():
    result = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert result.candle.symbol == "BTCUSDT"
    assert result.candle.timeframe == "1h"
    assert result.candle.open_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert str(result.candle.open) == "50000.00000000"
    assert str(result.candle.high) == "50500.00000000"
    assert str(result.candle.low) == "49500.00000000"
    assert str(result.candle.close) == "50200.00000000"
    assert str(result.candle.volume) == "12.50000000"


def test_parser_does_not_mutate_raw_input():
    raw_copy = copy.deepcopy(VALID_KLINE)

    parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")

    assert VALID_KLINE == raw_copy
