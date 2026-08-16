import copy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.binance import (
    BinanceHistoricalFundingRecord,
    parse_binance_historical_funding_record,
)

VALID_RAW = {
    "symbol": "BTCUSDT",
    "fundingRate": "0.00010000",
    "fundingTime": 1704067200000,
    "markPrice": "65000.12345678",
    "rateType": "Regular",
}


def _raw(**overrides: object) -> dict:
    merged = dict(VALID_RAW)
    merged.update(overrides)
    return merged


# --- valid cases ---


def test_valid_regular_record_is_parsed():
    result = parse_binance_historical_funding_record(VALID_RAW, expected_symbol="BTCUSDT")

    assert isinstance(result, BinanceHistoricalFundingRecord)
    assert result.symbol == "BTCUSDT"
    assert result.funding_rate == Decimal("0.00010000")
    assert result.mark_price == Decimal("65000.12345678")
    assert result.rate_type == "Regular"


def test_valid_special_record_is_parsed():
    result = parse_binance_historical_funding_record(
        _raw(rateType="Special"), expected_symbol="BTCUSDT"
    )

    assert result.rate_type == "Special"


def test_negative_funding_rate_is_legal():
    result = parse_binance_historical_funding_record(
        _raw(fundingRate="-0.00050000"), expected_symbol="BTCUSDT"
    )

    assert result.funding_rate == Decimal("-0.00050000")


def test_zero_funding_rate_is_legal():
    result = parse_binance_historical_funding_record(
        _raw(fundingRate="0"), expected_symbol="BTCUSDT"
    )

    assert result.funding_rate == Decimal(0)


def test_positive_funding_rate_is_legal():
    result = parse_binance_historical_funding_record(
        _raw(fundingRate="0.00030000"), expected_symbol="BTCUSDT"
    )

    assert result.funding_rate == Decimal("0.00030000")


def test_high_precision_funding_rate_is_preserved():
    result = parse_binance_historical_funding_record(
        _raw(fundingRate="0.000123456789"), expected_symbol="BTCUSDT"
    )

    assert result.funding_rate == Decimal("0.000123456789")


def test_high_precision_mark_price_is_preserved():
    result = parse_binance_historical_funding_record(
        _raw(markPrice="65000.123456789012"), expected_symbol="BTCUSDT"
    )

    assert result.mark_price == Decimal("65000.123456789012")


def test_funding_time_ms_converts_to_exact_utc_datetime():
    result = parse_binance_historical_funding_record(VALID_RAW, expected_symbol="BTCUSDT")

    assert result.funding_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert result.funding_time.tzinfo is not None
    assert result.funding_time.utcoffset().total_seconds() == 0


def test_record_is_frozen():
    result = parse_binance_historical_funding_record(VALID_RAW, expected_symbol="BTCUSDT")

    with pytest.raises(AttributeError):
        result.symbol = "ETHUSDT"  # type: ignore[misc]


def test_extra_unknown_field_is_tolerated():
    result = parse_binance_historical_funding_record(
        _raw(extraField="ignored"), expected_symbol="BTCUSDT"
    )

    assert isinstance(result, BinanceHistoricalFundingRecord)


def test_parser_does_not_mutate_raw_input():
    raw_copy = copy.deepcopy(VALID_RAW)

    parse_binance_historical_funding_record(VALID_RAW, expected_symbol="BTCUSDT")

    assert VALID_RAW == raw_copy


# --- input shape ---


@pytest.mark.parametrize("bad_raw", [[], (), "not-a-mapping", 123, None])
def test_non_mapping_raw_is_rejected(bad_raw):
    with pytest.raises(ValueError, match="mapping"):
        parse_binance_historical_funding_record(bad_raw, expected_symbol="BTCUSDT")


# --- required fields ---


@pytest.mark.parametrize("field", ["symbol", "fundingRate", "fundingTime", "markPrice", "rateType"])
def test_missing_required_field_is_rejected(field):
    raw = dict(VALID_RAW)
    del raw[field]

    with pytest.raises(ValueError, match="missing required fields"):
        parse_binance_historical_funding_record(raw, expected_symbol="BTCUSDT")


# --- fundingRate decimal invalid matrix ---


def test_funding_rate_json_float_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(_raw(fundingRate=0.0001), expected_symbol="BTCUSDT")


def test_funding_rate_json_int_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(_raw(fundingRate=0), expected_symbol="BTCUSDT")


def test_funding_rate_bool_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(_raw(fundingRate=True), expected_symbol="BTCUSDT")


def test_funding_rate_none_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(_raw(fundingRate=None), expected_symbol="BTCUSDT")


def test_funding_rate_malformed_string_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(
            _raw(fundingRate="not-a-number"), expected_symbol="BTCUSDT"
        )


def test_funding_rate_nan_string_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(_raw(fundingRate="NaN"), expected_symbol="BTCUSDT")


def test_funding_rate_infinity_string_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(
            _raw(fundingRate="Infinity"), expected_symbol="BTCUSDT"
        )


def test_funding_rate_negative_infinity_string_is_rejected():
    with pytest.raises(ValueError, match="fundingRate"):
        parse_binance_historical_funding_record(
            _raw(fundingRate="-Infinity"), expected_symbol="BTCUSDT"
        )


# --- markPrice decimal invalid matrix ---


def test_mark_price_json_number_is_rejected():
    with pytest.raises(ValueError, match="markPrice"):
        parse_binance_historical_funding_record(_raw(markPrice=65000.0), expected_symbol="BTCUSDT")


def test_mark_price_zero_string_is_rejected():
    with pytest.raises(ValueError, match="markPrice"):
        parse_binance_historical_funding_record(_raw(markPrice="0"), expected_symbol="BTCUSDT")


def test_mark_price_negative_string_is_rejected():
    with pytest.raises(ValueError, match="markPrice"):
        parse_binance_historical_funding_record(_raw(markPrice="-1"), expected_symbol="BTCUSDT")


def test_mark_price_nan_string_is_rejected():
    with pytest.raises(ValueError, match="markPrice"):
        parse_binance_historical_funding_record(_raw(markPrice="NaN"), expected_symbol="BTCUSDT")


def test_mark_price_infinity_string_is_rejected():
    with pytest.raises(ValueError, match="markPrice"):
        parse_binance_historical_funding_record(
            _raw(markPrice="Infinity"), expected_symbol="BTCUSDT"
        )


# --- fundingTime invalid matrix ---


def test_funding_time_string_is_rejected():
    with pytest.raises(ValueError, match="fundingTime"):
        parse_binance_historical_funding_record(
            _raw(fundingTime="1704067200000"), expected_symbol="BTCUSDT"
        )


def test_funding_time_float_is_rejected():
    with pytest.raises(ValueError, match="fundingTime"):
        parse_binance_historical_funding_record(
            _raw(fundingTime=1704067200000.0), expected_symbol="BTCUSDT"
        )


def test_funding_time_bool_is_rejected():
    with pytest.raises(ValueError, match="fundingTime"):
        parse_binance_historical_funding_record(_raw(fundingTime=True), expected_symbol="BTCUSDT")


def test_funding_time_negative_is_rejected():
    with pytest.raises(ValueError, match="fundingTime"):
        parse_binance_historical_funding_record(_raw(fundingTime=-1), expected_symbol="BTCUSDT")


def test_funding_time_overflow_is_rejected():
    with pytest.raises(ValueError, match="fundingTime"):
        parse_binance_historical_funding_record(_raw(fundingTime=10**30), expected_symbol="BTCUSDT")


# --- rateType strictness ---


@pytest.mark.parametrize(
    "bad_rate_type",
    ["regular", "SPECIAL", "Regular ", " standard", "standard", "", 123, None],
)
def test_invalid_rate_type_is_rejected(bad_rate_type):
    with pytest.raises(ValueError, match="rateType"):
        parse_binance_historical_funding_record(
            _raw(rateType=bad_rate_type), expected_symbol="BTCUSDT"
        )


# --- symbol ---


@pytest.mark.parametrize("bad_symbol", [123, None, "", "   "])
def test_invalid_symbol_is_rejected(bad_symbol):
    with pytest.raises(ValueError, match="symbol"):
        parse_binance_historical_funding_record(_raw(symbol=bad_symbol), expected_symbol="BTCUSDT")


def test_symbol_mismatch_is_rejected():
    with pytest.raises(ValueError, match="mismatch"):
        parse_binance_historical_funding_record(VALID_RAW, expected_symbol="ETHUSDT")


def test_valid_symbol_is_preserved_exactly():
    result = parse_binance_historical_funding_record(VALID_RAW, expected_symbol="BTCUSDT")

    assert result.symbol == "BTCUSDT"


# --- expected_symbol independently validated ---


@pytest.mark.parametrize("bad_expected_symbol", ["", "   ", 123])
def test_malformed_expected_symbol_is_rejected(bad_expected_symbol):
    with pytest.raises(ValueError, match="symbol"):
        parse_binance_historical_funding_record(VALID_RAW, expected_symbol=bad_expected_symbol)


def test_expected_symbol_case_mismatch_is_not_normalized():
    with pytest.raises(ValueError, match="mismatch"):
        parse_binance_historical_funding_record(VALID_RAW, expected_symbol="btcusdt")
