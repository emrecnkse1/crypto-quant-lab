from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.parsers import parse_binance_kline

VALID_BTCUSDT_KLINE = [
    1704067200000,  # open time ms -> 2024-01-01T00:00:00Z
    "50000.00000000",
    "50500.12345678",
    "49500.00000000",
    "50200.00000000",
    "12.50000000",
    1704070799999,  # close time
    "625000.00",  # quote asset volume
    100,  # number of trades
    "6.00000000",  # taker buy base volume
    "300000.00",  # taker buy quote volume
    "0",  # ignore
]


def test_valid_binance_btcusdt_kline_is_parsed():
    candle = parse_binance_kline(VALID_BTCUSDT_KLINE, symbol="BTCUSDT", timeframe="1h")
    assert candle.symbol == "BTCUSDT"
    assert candle.timeframe == "1h"


def test_open_time_is_utc_timezone_aware():
    candle = parse_binance_kline(VALID_BTCUSDT_KLINE, symbol="BTCUSDT", timeframe="1h")
    assert candle.open_time == datetime(2024, 1, 1, tzinfo=UTC)
    assert candle.open_time.tzinfo is UTC


def test_ohlcv_values_are_decimal():
    candle = parse_binance_kline(VALID_BTCUSDT_KLINE, symbol="BTCUSDT", timeframe="1h")
    for value in (candle.open, candle.high, candle.low, candle.close, candle.volume):
        assert isinstance(value, Decimal)


def test_string_prices_preserve_exact_value():
    candle = parse_binance_kline(VALID_BTCUSDT_KLINE, symbol="BTCUSDT", timeframe="1h")
    assert candle.high == Decimal("50500.12345678")
    assert str(candle.high) == "50500.12345678"


def test_kline_shorter_than_six_fields_is_rejected():
    with pytest.raises(ValueError, match="at least 6"):
        parse_binance_kline([1704067200000, "1", "2", "1"], symbol="BTCUSDT", timeframe="1h")


def test_invalid_timestamp_is_rejected():
    kline = list(VALID_BTCUSDT_KLINE)
    kline[0] = "not-a-timestamp"
    with pytest.raises(ValueError, match="timestamp"):
        parse_binance_kline(kline, symbol="BTCUSDT", timeframe="1h")


def test_invalid_price_is_rejected():
    kline = list(VALID_BTCUSDT_KLINE)
    kline[2] = "not-a-price"
    with pytest.raises(ValueError, match="high"):
        parse_binance_kline(kline, symbol="BTCUSDT", timeframe="1h")


def test_invalid_volume_is_rejected():
    kline = list(VALID_BTCUSDT_KLINE)
    kline[5] = None
    with pytest.raises(ValueError, match="volume"):
        parse_binance_kline(kline, symbol="BTCUSDT", timeframe="1h")


def test_candle_domain_validation_still_applies():
    kline = list(VALID_BTCUSDT_KLINE)
    kline[2] = "1000.00000000"  # high below open/low/close -> Candle should reject it
    with pytest.raises(ValueError, match="high"):
        parse_binance_kline(kline, symbol="BTCUSDT", timeframe="1h")
