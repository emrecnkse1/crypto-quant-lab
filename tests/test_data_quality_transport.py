from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from crypto_quant_lab.data_quality.transport import (
    is_open_time_in_range,
    transport_end_ms,
    transport_start_ms,
)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


_EXPECTED_MS_AT_14_00_00 = 1704117600000  # 2024-01-01T14:00:00Z


# --- transport_start_ms ---


def test_transport_start_ms_exact_millisecond_unchanged():
    result = transport_start_ms(datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00


def test_transport_start_ms_floors_sub_millisecond_offset():
    result = transport_start_ms(datetime(2024, 1, 1, 14, 0, 0, 500, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00


def test_transport_start_ms_normalizes_non_utc_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    same_instant = datetime(2024, 1, 1, 19, 0, 0, tzinfo=plus_five)  # == 14:00 UTC
    assert transport_start_ms(same_instant) == _EXPECTED_MS_AT_14_00_00


def test_transport_start_ms_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        transport_start_ms(datetime(2024, 1, 1, 14, 0, 0))  # noqa: DTZ001


def test_transport_start_ms_rejects_pseudo_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        transport_start_ms(datetime(2024, 1, 1, 14, 0, 0, tzinfo=_BrokenTzInfo()))


# --- transport_end_ms ---


def test_transport_end_ms_exact_millisecond_unchanged():
    result = transport_end_ms(datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00


def test_transport_end_ms_ceils_500_microsecond_offset():
    result = transport_end_ms(datetime(2024, 1, 1, 14, 0, 0, 500, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00 + 1


def test_transport_end_ms_ceils_1_microsecond_offset():
    result = transport_end_ms(datetime(2024, 1, 1, 14, 0, 0, 1, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00 + 1


def test_transport_end_ms_ceils_999_microsecond_offset():
    result = transport_end_ms(datetime(2024, 1, 1, 14, 0, 0, 999, tzinfo=UTC))
    assert result == _EXPECTED_MS_AT_14_00_00 + 1


def test_transport_end_ms_normalizes_non_utc_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    same_instant = datetime(2024, 1, 1, 19, 0, 0, tzinfo=plus_five)  # == 14:00 UTC
    assert transport_end_ms(same_instant) == _EXPECTED_MS_AT_14_00_00


def test_transport_end_ms_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        transport_end_ms(datetime(2024, 1, 1, 14, 0, 0))  # noqa: DTZ001


def test_transport_end_ms_rejects_pseudo_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        transport_end_ms(datetime(2024, 1, 1, 14, 0, 0, tzinfo=_BrokenTzInfo()))


# --- sub-millisecond underfetch regression (DATA_QUALITY_SPEC.md Bölüm 7) ---


def test_transport_widening_does_not_underfetch_sub_millisecond_exclusive_end():
    effective_end = datetime(2024, 1, 1, 14, 0, 0, 500, tzinfo=UTC)
    candidate_open_time = datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC)

    requested_end_ms = transport_end_ms(effective_end)
    candidate_ms = transport_start_ms(candidate_open_time)

    # A transport request up to (inclusive, per Binance) requested_end_ms must
    # still cover the candidate's own millisecond bucket — otherwise it would
    # be silently underfetched before the canonical filter even sees it.
    assert requested_end_ms >= candidate_ms
    assert requested_end_ms == _EXPECTED_MS_AT_14_00_00 + 1
    assert candidate_ms == _EXPECTED_MS_AT_14_00_00

    # The canonical filter is what ultimately decides inclusion, and it does
    # include this candle exactly because it is a true microsecond compare.
    assert is_open_time_in_range(
        candidate_open_time,
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        effective_end=effective_end,
    )


# --- is_open_time_in_range (canonical filter) ---


_REQUESTED_START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
_EFFECTIVE_END = datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def test_canonical_filter_just_before_start_is_false():
    just_before = datetime(2024, 1, 1, 9, 59, 59, 999999, tzinfo=UTC)
    assert is_open_time_in_range(just_before, _REQUESTED_START, _EFFECTIVE_END) is False


def test_canonical_filter_at_start_is_true():
    assert is_open_time_in_range(_REQUESTED_START, _REQUESTED_START, _EFFECTIVE_END) is True


def test_canonical_filter_just_before_end_is_true():
    just_before_end = datetime(2024, 1, 1, 13, 59, 59, 999999, tzinfo=UTC)
    assert is_open_time_in_range(just_before_end, _REQUESTED_START, _EFFECTIVE_END) is True


def test_canonical_filter_at_end_is_false():
    assert is_open_time_in_range(_EFFECTIVE_END, _REQUESTED_START, _EFFECTIVE_END) is False


def test_canonical_filter_normalizes_non_utc_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    same_instant_as_start = datetime(2024, 1, 1, 15, 0, tzinfo=plus_five)  # == 10:00 UTC
    assert is_open_time_in_range(same_instant_as_start, _REQUESTED_START, _EFFECTIVE_END) is True


def test_canonical_filter_rejects_equal_start_and_effective_end():
    same_time = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="requested_start"):
        is_open_time_in_range(same_time, same_time, same_time)


def test_canonical_filter_rejects_start_after_effective_end():
    with pytest.raises(ValueError, match="requested_start"):
        is_open_time_in_range(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            requested_start=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            effective_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        )


def test_canonical_filter_rejects_naive_open_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_open_time_in_range(
            datetime(2024, 1, 1, 11, 0),  # noqa: DTZ001
            _REQUESTED_START,
            _EFFECTIVE_END,
        )


def test_canonical_filter_rejects_pseudo_naive_requested_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_open_time_in_range(
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=_BrokenTzInfo()),
            effective_end=_EFFECTIVE_END,
        )
