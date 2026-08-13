from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from crypto_quant_lab.data_quality.time import (
    calculate_effective_end,
    incomplete_tail_excluded_count,
    is_grid_aligned,
    latest_closed_boundary,
)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


# --- is_grid_aligned ---


def test_1h_aligned_timestamp_is_true():
    assert is_grid_aligned(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), "1h") is True


def test_1h_thirty_minute_offset_is_false():
    assert is_grid_aligned(datetime(2024, 1, 1, 10, 30, tzinfo=UTC), "1h") is False


def test_4h_aligned_timestamp_is_true():
    assert is_grid_aligned(datetime(2024, 1, 1, 12, 0, tzinfo=UTC), "4h") is True


def test_4h_non_aligned_timestamp_is_false():
    assert is_grid_aligned(datetime(2024, 1, 1, 14, 0, tzinfo=UTC), "4h") is False


def test_grid_alignment_normalizes_non_utc_aware_equivalent_instant():
    plus_five = timezone(timedelta(hours=5))
    # 10:00 UTC == 15:00 at UTC+5, both 1h-grid-aligned
    same_instant = datetime(2024, 1, 1, 15, 0, tzinfo=plus_five)
    assert is_grid_aligned(same_instant, "1h") is True


def test_grid_alignment_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_grid_aligned(datetime(2024, 1, 1, 10, 0), "1h")  # noqa: DTZ001


def test_grid_alignment_rejects_pseudo_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        is_grid_aligned(datetime(2024, 1, 1, 10, 0, tzinfo=_BrokenTzInfo()), "1h")


def test_grid_alignment_rejects_unsupported_timeframe():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        is_grid_aligned(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), "15m")


# --- latest_closed_boundary ---


def test_latest_closed_boundary_1h_mid_hour():
    result = latest_closed_boundary(datetime(2024, 1, 1, 14, 37, tzinfo=UTC), "1h")
    assert result == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def test_latest_closed_boundary_4h_mid_period():
    result = latest_closed_boundary(datetime(2024, 1, 1, 14, 37, tzinfo=UTC), "4h")
    assert result == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_latest_closed_boundary_exact_boundary_is_inclusive():
    result = latest_closed_boundary(datetime(2024, 1, 1, 16, 0, tzinfo=UTC), "4h")
    assert result == datetime(2024, 1, 1, 16, 0, tzinfo=UTC)


def test_latest_closed_boundary_result_is_utc_timezone_aware():
    result = latest_closed_boundary(datetime(2024, 1, 1, 14, 37, tzinfo=UTC), "1h")
    assert result.tzinfo is UTC


def test_latest_closed_boundary_normalizes_non_utc_aware_as_of():
    plus_five = timezone(timedelta(hours=5))
    as_of_local = datetime(2024, 1, 1, 19, 37, tzinfo=plus_five)  # == 14:37 UTC
    result = latest_closed_boundary(as_of_local, "1h")
    assert result == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def test_latest_closed_boundary_rejects_naive_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_closed_boundary(datetime(2024, 1, 1, 14, 37), "1h")  # noqa: DTZ001


def test_latest_closed_boundary_rejects_pseudo_naive_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_closed_boundary(datetime(2024, 1, 1, 14, 37, tzinfo=_BrokenTzInfo()), "1h")


# --- calculate_effective_end ---


def test_effective_end_reference_fixture_from_spec():
    result = calculate_effective_end(
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        timeframe="1h",
    )
    assert result == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def test_effective_end_uses_requested_end_when_earlier_than_closed_boundary():
    result = calculate_effective_end(
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        timeframe="1h",
    )
    assert result == datetime(2024, 1, 1, 13, 0, tzinfo=UTC)


def test_effective_end_rejects_unaligned_requested_start():
    with pytest.raises(ValueError, match="requested_start"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 10, 30, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_rejects_unaligned_requested_end():
    with pytest.raises(ValueError, match="requested_end"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 15, 30, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_rejects_equal_start_and_end():
    with pytest.raises(ValueError, match="requested_start"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_rejects_reversed_range():
    with pytest.raises(ValueError, match="requested_start"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_rejects_when_equal_to_requested_start():
    # as_of exactly at requested_start: latest_closed_boundary == requested_start
    with pytest.raises(ValueError, match="finalized"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_rejects_when_before_requested_start():
    # as_of before requested_start entirely: latest_closed_boundary < requested_start
    with pytest.raises(ValueError, match="finalized"):
        calculate_effective_end(
            requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            as_of_time=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
            timeframe="1h",
        )


def test_effective_end_accepts_arbitrary_non_grid_as_of_time():
    result = calculate_effective_end(
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, 12, 345678, tzinfo=UTC),
        timeframe="1h",
    )
    assert result == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


def test_effective_end_normalizes_non_utc_requested_boundaries():
    plus_five = timezone(timedelta(hours=5))
    result = calculate_effective_end(
        requested_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
        requested_end=datetime(2024, 1, 1, 20, 0, tzinfo=plus_five),  # == 15:00 UTC
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        timeframe="1h",
    )
    assert result == datetime(2024, 1, 1, 14, 0, tzinfo=UTC)


# --- incomplete_tail_excluded_count ---


def test_incomplete_tail_one_hour_gap_at_1h():
    count = incomplete_tail_excluded_count(
        effective_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        timeframe="1h",
    )
    assert count == 1


def test_incomplete_tail_zero_when_equal():
    count = incomplete_tail_excluded_count(
        effective_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        timeframe="1h",
    )
    assert count == 0


def test_incomplete_tail_two_periods_at_4h():
    count = incomplete_tail_excluded_count(
        effective_end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        timeframe="4h",
    )
    assert count == 2


def test_incomplete_tail_rejects_reversed_boundaries():
    with pytest.raises(ValueError, match="requested_end"):
        incomplete_tail_excluded_count(
            effective_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
            timeframe="1h",
        )


def test_incomplete_tail_rejects_unaligned_boundary():
    with pytest.raises(ValueError, match="effective_end"):
        incomplete_tail_excluded_count(
            effective_end=datetime(2024, 1, 1, 14, 30, tzinfo=UTC),
            requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
            timeframe="1h",
        )
