import json
import urllib.parse

import pytest

from crypto_quant_lab.market_data import binance_public
from crypto_quant_lab.market_data.binance_historical import (
    BinanceHistoricalKline,
    parse_binance_historical_kline,
)
from crypto_quant_lab.market_data.binance_public import (
    fetch_binance_historical_klines,
    fetch_binance_klines,
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


def _fake_request_returning(body: bytes):
    def _fake(url: str, timeout: float) -> bytes:
        _fake.last_url = url
        _fake.last_timeout = timeout
        return body

    return _fake


def test_valid_response_returns_candle_list(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    candles = fetch_binance_klines("BTCUSDT", "1h", limit=1)

    assert len(candles) == 1
    assert isinstance(candles[0], Candle)
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].timeframe == "1h"


def test_url_contains_symbol_interval_and_limit(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines("ETHUSDT", "4h", limit=5)

    parsed = urllib.parse.urlparse(fake.last_url)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "data-api.binance.vision"
    assert parsed.path == "/api/v3/klines"
    assert query["symbol"] == ["ETHUSDT"]
    assert query["interval"] == ["4h"]
    assert query["limit"] == ["5"]


def test_empty_symbol_is_rejected():
    with pytest.raises(ValueError, match="symbol"):
        fetch_binance_klines("", "1h")


def test_empty_timeframe_is_rejected():
    with pytest.raises(ValueError, match="timeframe"):
        fetch_binance_klines("BTCUSDT", "")


def test_limit_below_minimum_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_klines("BTCUSDT", "1h", limit=0)


def test_limit_above_maximum_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_klines("BTCUSDT", "1h", limit=1001)


def test_non_positive_timeout_is_rejected():
    with pytest.raises(ValueError, match="timeout"):
        fetch_binance_klines("BTCUSDT", "1h", timeout=0)


def test_invalid_json_is_rejected(monkeypatch):
    fake = _fake_request_returning(b"not-json")
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(ValueError, match="JSON"):
        fetch_binance_klines("BTCUSDT", "1h")


def test_non_list_json_response_is_rejected(monkeypatch):
    fake = _fake_request_returning(json.dumps({"error": "unexpected"}).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(ValueError, match="list"):
        fetch_binance_klines("BTCUSDT", "1h")


def test_network_error_is_reported_clearly(monkeypatch):
    def _raise(url: str, timeout: float) -> bytes:
        raise ConnectionError("failed to reach Binance public API: simulated network failure")

    monkeypatch.setattr(binance_public, "_request", _raise)

    with pytest.raises(ConnectionError, match="Binance public API"):
        fetch_binance_klines("BTCUSDT", "1h")


def test_no_range_params_means_no_start_or_end_in_query(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines("BTCUSDT", "1h", limit=1)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert "startTime" not in query
    assert "endTime" not in query


def test_start_time_ms_is_added_to_query(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines("BTCUSDT", "1h", limit=1, start_time_ms=1234567890000)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["startTime"] == ["1234567890000"]
    assert "endTime" not in query


def test_end_time_ms_is_added_to_query(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines("BTCUSDT", "1h", limit=1, end_time_ms=1234567990000)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["endTime"] == ["1234567990000"]
    assert "startTime" not in query


def test_start_and_end_time_ms_are_both_added_to_query(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines(
        "BTCUSDT", "1h", limit=1, start_time_ms=1234567890000, end_time_ms=1234567990000
    )

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["startTime"] == ["1234567890000"]
    assert query["endTime"] == ["1234567990000"]
    assert query["symbol"] == ["BTCUSDT"]
    assert query["interval"] == ["1h"]
    assert query["limit"] == ["1"]


def test_start_time_ms_bool_is_rejected(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(TypeError, match="start_time_ms"):
        fetch_binance_klines("BTCUSDT", "1h", start_time_ms=True)


def test_end_time_ms_bool_is_rejected(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(TypeError, match="end_time_ms"):
        fetch_binance_klines("BTCUSDT", "1h", end_time_ms=False)


def test_start_time_ms_float_is_rejected():
    with pytest.raises(TypeError, match="start_time_ms"):
        fetch_binance_klines("BTCUSDT", "1h", start_time_ms=1234567890000.0)


def test_start_time_ms_string_is_rejected():
    with pytest.raises(TypeError, match="start_time_ms"):
        fetch_binance_klines("BTCUSDT", "1h", start_time_ms="1234567890000")


def test_start_after_end_is_rejected():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_klines(
            "BTCUSDT", "1h", start_time_ms=1234567990000, end_time_ms=1234567890000
        )


def test_start_equal_end_is_accepted_by_low_level_client(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_klines("BTCUSDT", "1h", start_time_ms=1234567890000, end_time_ms=1234567890000)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["startTime"] == ["1234567890000"]
    assert query["endTime"] == ["1234567890000"]


def test_range_params_do_not_change_candle_parsing(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    candles = fetch_binance_klines(
        "BTCUSDT", "1h", limit=1, start_time_ms=1234567890000, end_time_ms=1234567990000
    )

    assert len(candles) == 1
    assert isinstance(candles[0], Candle)
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].timeframe == "1h"


# --- fetch_binance_historical_klines ---


def test_historical_valid_response_returns_binance_historical_kline_list(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    klines = fetch_binance_historical_klines("BTCUSDT", "1h", limit=1)

    assert len(klines) == 1
    assert isinstance(klines[0], BinanceHistoricalKline)


def test_historical_raw_close_time_is_preserved(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    klines = fetch_binance_historical_klines("BTCUSDT", "1h", limit=1)

    expected = parse_binance_historical_kline(VALID_KLINE, "BTCUSDT", "1h")
    assert klines[0].close_time == expected.close_time


def test_historical_candle_matches_existing_parser(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    klines = fetch_binance_historical_klines("BTCUSDT", "1h", limit=1)

    expected_candle = parse_binance_kline(VALID_KLINE, "BTCUSDT", "1h")
    assert klines[0].candle == expected_candle


def test_historical_url_contains_start_and_end_time(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_historical_klines(
        "BTCUSDT", "1h", limit=1, start_time_ms=1234567890000, end_time_ms=1234567990000
    )

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["startTime"] == ["1234567890000"]
    assert query["endTime"] == ["1234567990000"]


def test_historical_url_contains_symbol_interval_and_limit(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_historical_klines("ETHUSDT", "4h", limit=500)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["symbol"] == ["ETHUSDT"]
    assert query["interval"] == ["4h"]
    assert query["limit"] == ["500"]


def test_historical_default_limit_is_1000(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_KLINE]).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    fetch_binance_historical_klines("BTCUSDT", "1h")

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["limit"] == ["1000"]


def test_historical_invalid_json_is_rejected(monkeypatch):
    fake = _fake_request_returning(b"not-json")
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(ValueError, match="JSON"):
        fetch_binance_historical_klines("BTCUSDT", "1h")


def test_historical_non_list_json_response_is_rejected(monkeypatch):
    fake = _fake_request_returning(json.dumps({"error": "unexpected"}).encode())
    monkeypatch.setattr(binance_public, "_request", fake)

    with pytest.raises(ValueError, match="list"):
        fetch_binance_historical_klines("BTCUSDT", "1h")


def test_historical_network_error_is_reported_clearly(monkeypatch):
    def _raise(url: str, timeout: float) -> bytes:
        raise ConnectionError("failed to reach Binance public API: simulated network failure")

    monkeypatch.setattr(binance_public, "_request", _raise)

    with pytest.raises(ConnectionError, match="Binance public API"):
        fetch_binance_historical_klines("BTCUSDT", "1h")
