from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.storage.base import DataCorruptionError
from crypto_quant_lab.storage.sqlite_codec import (
    datetime_to_epoch_us,
    decimal_to_text,
    epoch_us_to_datetime,
    text_to_decimal,
)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


# --- Decimal <-> TEXT ---


def test_high_precision_decimal_round_trips_exactly():
    original = Decimal("50000.12345678")
    text = decimal_to_text(original)
    assert text == "50000.12345678"
    assert text_to_decimal(text) == original


def test_trailing_zeros_are_preserved():
    original = Decimal("1.2300")
    text = decimal_to_text(original)
    assert text == "1.2300"
    assert text_to_decimal(text) == original
    assert str(text_to_decimal(text)) == "1.2300"


def test_very_small_decimal_round_trips_exactly():
    original = Decimal("0.00000001")
    assert text_to_decimal(decimal_to_text(original)) == original


def test_very_large_decimal_round_trips_exactly():
    original = Decimal("123456789012345678901234567890.12345678")
    assert text_to_decimal(decimal_to_text(original)) == original


def test_negative_decimal_round_trips_exactly():
    original = Decimal("-42.5")
    assert text_to_decimal(decimal_to_text(original)) == original


def test_zero_decimal_round_trips_exactly():
    original = Decimal(0)
    assert text_to_decimal(decimal_to_text(original)) == original


def test_decimal_to_text_rejects_non_decimal_input():
    with pytest.raises(TypeError):
        decimal_to_text(1.5)


def test_text_to_decimal_rejects_non_string_input():
    with pytest.raises(TypeError):
        text_to_decimal(Decimal(1))


def test_text_to_decimal_raises_data_corruption_error_for_malformed_text():
    with pytest.raises(DataCorruptionError):
        text_to_decimal("not-a-number")


def test_malformed_decimal_text_does_not_fall_back_to_default():
    try:
        text_to_decimal("not-a-number")
    except DataCorruptionError:
        pass
    else:
        pytest.fail("expected DataCorruptionError, got a value instead")


NON_FINITE_DECIMALS = [
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
]

NON_FINITE_DECIMAL_TEXTS = ["NaN", "sNaN", "Infinity", "-Infinity"]


@pytest.mark.parametrize("value", NON_FINITE_DECIMALS, ids=str)
def test_decimal_to_text_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        decimal_to_text(value)


@pytest.mark.parametrize("text", NON_FINITE_DECIMAL_TEXTS)
def test_text_to_decimal_rejects_non_finite_stored_text(text):
    with pytest.raises(DataCorruptionError):
        text_to_decimal(text)


# --- datetime <-> epoch microseconds ---


def test_utc_epoch_is_exactly_zero():
    assert datetime_to_epoch_us(datetime(1970, 1, 1, tzinfo=UTC)) == 0


def test_epoch_plus_one_microsecond_is_one():
    assert datetime_to_epoch_us(datetime(1970, 1, 1, microsecond=1, tzinfo=UTC)) == 1


def test_microsecond_component_round_trips_exactly():
    original = datetime(2024, 5, 1, 10, 20, 30, 123456, tzinfo=UTC)
    epoch_us = datetime_to_epoch_us(original)
    assert epoch_us_to_datetime(epoch_us) == original
    assert epoch_us_to_datetime(epoch_us).microsecond == 123456


def test_normal_modern_utc_datetime_round_trips_exactly():
    original = datetime(2024, 6, 15, 12, 30, 45, 987654, tzinfo=UTC)
    assert epoch_us_to_datetime(datetime_to_epoch_us(original)) == original


def test_pre_1970_datetime_round_trips_via_negative_epoch():
    original = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    epoch_us = datetime_to_epoch_us(original)
    assert epoch_us == -1
    assert epoch_us_to_datetime(epoch_us) == original


def test_non_utc_timezone_normalizes_to_same_instant():
    utc_dt = datetime(2024, 3, 10, 12, 0, 0, tzinfo=UTC)
    plus_five = timezone(timedelta(hours=5))
    same_instant_dt = datetime(2024, 3, 10, 17, 0, 0, tzinfo=plus_five)

    assert datetime_to_epoch_us(utc_dt) == datetime_to_epoch_us(same_instant_dt)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        datetime_to_epoch_us(datetime(2024, 1, 1))  # noqa: DTZ001


def test_pseudo_naive_datetime_with_none_utcoffset_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        datetime_to_epoch_us(datetime(2024, 1, 1, tzinfo=_BrokenTzInfo()))


def test_datetime_to_epoch_us_rejects_non_datetime_input():
    with pytest.raises(TypeError):
        datetime_to_epoch_us("2024-01-01")


def test_epoch_us_to_datetime_rejects_non_int_input():
    with pytest.raises(TypeError):
        epoch_us_to_datetime(1.0)


def test_epoch_us_to_datetime_rejects_bool_input():
    with pytest.raises(TypeError):
        epoch_us_to_datetime(True)


def test_out_of_range_epoch_us_raises_data_corruption_error():
    with pytest.raises(DataCorruptionError):
        epoch_us_to_datetime(10**30)


def test_epoch_us_to_datetime_result_is_utc_timezone_aware():
    result = epoch_us_to_datetime(0)
    assert result.tzinfo is UTC


# --- float precision regression guard ---


def test_far_future_datetime_has_no_float_precision_loss():
    original = datetime(2500, 7, 4, 3, 2, 1, 123457, tzinfo=UTC)
    epoch_us = datetime_to_epoch_us(original)
    round_tripped = epoch_us_to_datetime(epoch_us)

    assert round_tripped == original
    assert round_tripped.microsecond == 123457
