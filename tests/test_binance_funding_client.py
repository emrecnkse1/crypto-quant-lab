import json
import urllib.parse

import pytest

from crypto_quant_lab.funding import binance as binance_funding
from crypto_quant_lab.funding.binance import (
    BinanceHistoricalFundingRecord,
    fetch_binance_funding_rate_page,
)

VALID_ROW = {
    "symbol": "BTCUSDT",
    "fundingRate": "0.00010000",
    "fundingTime": 1704067200000,
    "markPrice": "65000.12345678",
    "rateType": "Regular",
}


def _fake_request_returning(body: bytes):
    def _fake(url: str, timeout: float) -> bytes:
        _fake.calls = getattr(_fake, "calls", 0) + 1
        _fake.last_url = url
        _fake.last_timeout = timeout
        return body

    return _fake


# --- request shape ---


def test_request_targets_futures_host_and_path(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    parsed = urllib.parse.urlparse(fake.last_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "fapi.binance.com"
    assert parsed.netloc != "data-api.binance.vision"
    assert parsed.path == "/fapi/v1/fundingRate"


def test_request_query_contains_exact_params_and_no_auth(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["symbol"] == ["BTCUSDT"]
    assert query["startTime"] == ["1000"]
    assert query["endTime"] == ["2000"]
    assert query["limit"] == ["100"]
    assert "recvWindow" not in query
    assert "timestamp" not in query
    assert "signature" not in query


# --- time bound validation ---


def test_start_before_end_is_legal(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    result = fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert len(result) == 1


def test_start_equal_end_is_legal(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    fetch_binance_funding_rate_page("BTCUSDT", 1000, 1000, 100)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["startTime"] == ["1000"]
    assert query["endTime"] == ["1000"]


def test_start_after_end_is_rejected():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_rate_page("BTCUSDT", 2000, 1000, 100)


def test_negative_start_time_is_rejected():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_rate_page("BTCUSDT", -1, 1000, 100)


def test_negative_end_time_is_rejected():
    with pytest.raises(ValueError, match="end_time_ms"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, -1, 100)


def test_start_time_bool_is_rejected():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_rate_page("BTCUSDT", True, 1000, 100)


def test_end_time_bool_is_rejected():
    with pytest.raises(ValueError, match="end_time_ms"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, False, 100)


# --- limit validation ---


def test_limit_of_one_is_legal(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 1)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["limit"] == ["1"]


def test_limit_of_1000_is_legal(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 1000)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(fake.last_url).query)
    assert query["limit"] == ["1000"]


def test_limit_zero_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 0)


def test_limit_above_1000_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 1001)


def test_limit_bool_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, True)


def test_limit_non_int_is_rejected():
    with pytest.raises(ValueError, match="limit"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100.0)


# --- symbol validation ---


def test_empty_symbol_is_rejected():
    with pytest.raises(ValueError, match="symbol"):
        fetch_binance_funding_rate_page("", 1000, 2000, 100)


# --- response parsing ---


def test_valid_page_returns_record_list_in_order(monkeypatch):
    row_a = dict(VALID_ROW, fundingTime=1704067200000)
    row_b = dict(VALID_ROW, fundingTime=1704096000000)
    fake = _fake_request_returning(json.dumps([row_a, row_b]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    result = fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert len(result) == 2
    assert all(isinstance(r, BinanceHistoricalFundingRecord) for r in result)
    assert result[0].funding_time < result[1].funding_time


# --- malformed top level ---


def test_dict_top_level_is_rejected(monkeypatch):
    fake = _fake_request_returning(json.dumps({"unexpected": "shape"}).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    with pytest.raises(ValueError, match="list"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)


def test_null_top_level_is_rejected(monkeypatch):
    fake = _fake_request_returning(b"null")
    monkeypatch.setattr(binance_funding, "_request", fake)

    with pytest.raises(ValueError, match="list"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)


def test_invalid_json_is_rejected(monkeypatch):
    fake = _fake_request_returning(b"not-json")
    monkeypatch.setattr(binance_funding, "_request", fake)

    with pytest.raises(ValueError, match="JSON"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)


# --- malformed row / partial parse ---


def test_one_malformed_row_fails_entire_page(monkeypatch):
    valid_row = dict(VALID_ROW)
    invalid_row = dict(VALID_ROW, rateType="unknown")
    fake = _fake_request_returning(json.dumps([valid_row, invalid_row, valid_row]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    with pytest.raises(ValueError, match="rateType"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)


# --- connection failure ---


def test_connection_error_propagates_without_retry(monkeypatch):
    def _raise(url: str, timeout: float) -> bytes:
        _raise.calls = getattr(_raise, "calls", 0) + 1
        raise ConnectionError("failed to reach Binance Futures API: simulated network failure")

    monkeypatch.setattr(binance_funding, "_request", _raise)

    with pytest.raises(ConnectionError, match="Binance Futures API"):
        fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert _raise.calls == 1


# --- order preservation ---


def test_out_of_order_response_is_returned_as_received(monkeypatch):
    later_row = dict(VALID_ROW, fundingTime=1704096000000)
    earlier_row = dict(VALID_ROW, fundingTime=1704067200000)
    fake = _fake_request_returning(json.dumps([later_row, earlier_row]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    result = fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert result[0].funding_time > result[1].funding_time


# --- duplicate preservation ---


def test_exact_duplicate_rows_are_both_preserved(monkeypatch):
    fake = _fake_request_returning(json.dumps([VALID_ROW, VALID_ROW]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    result = fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert len(result) == 2
    assert result[0] == result[1]


# --- same-time multi-type preservation ---


def test_same_funding_time_regular_and_special_both_returned(monkeypatch):
    regular_row = dict(VALID_ROW, rateType="Regular")
    special_row = dict(VALID_ROW, rateType="Special")
    fake = _fake_request_returning(json.dumps([regular_row, special_row]).encode())
    monkeypatch.setattr(binance_funding, "_request", fake)

    result = fetch_binance_funding_rate_page("BTCUSDT", 1000, 2000, 100)

    assert len(result) == 2
    assert result[0].rate_type == "Regular"
    assert result[1].rate_type == "Special"
    assert result[0].funding_time == result[1].funding_time
