from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from crypto_quant_lab.validation.windows import TemporalSplit, TemporalWindow


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


# --- TemporalWindow: valid construction ---


def test_valid_window_can_be_created():
    window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    )
    assert window.start == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert window.end == datetime(2024, 1, 1, 11, 0, tzinfo=UTC)


def test_window_non_utc_aware_equivalent_is_accepted():
    plus_five = timezone(timedelta(hours=5))
    start = datetime(2024, 1, 1, 5, 0, tzinfo=plus_five)  # == 2024-01-01 00:00 UTC
    end = datetime(2024, 1, 1, 6, 0, tzinfo=plus_five)  # == 2024-01-01 01:00 UTC
    window = TemporalWindow(start=start, end=end)
    assert window.start == start
    assert window.start.tzinfo is plus_five  # not silently converted/replaced
    assert window.end == end


# --- TemporalWindow: invalid construction ---


def test_window_non_datetime_start_is_rejected():
    with pytest.raises(TypeError):
        TemporalWindow(start="2024-01-01", end=datetime(2024, 1, 2, tzinfo=UTC))


def test_window_non_datetime_end_is_rejected():
    with pytest.raises(TypeError):
        TemporalWindow(start=datetime(2024, 1, 1, tzinfo=UTC), end="2024-01-02")


def test_window_naive_start_is_rejected():
    with pytest.raises(ValueError):
        TemporalWindow(start=datetime(2024, 1, 1), end=datetime(2024, 1, 2, tzinfo=UTC))  # noqa: DTZ001


def test_window_naive_end_is_rejected():
    with pytest.raises(ValueError):
        TemporalWindow(start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 1, 2))  # noqa: DTZ001


def test_window_pseudo_naive_start_is_rejected():
    with pytest.raises(ValueError):
        TemporalWindow(
            start=datetime(2024, 1, 1, tzinfo=_BrokenTzInfo()), end=datetime(2024, 1, 2, tzinfo=UTC)
        )


def test_window_start_equal_end_is_rejected():
    same = datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="start"):
        TemporalWindow(start=same, end=same)


def test_window_start_after_end_is_rejected():
    with pytest.raises(ValueError, match="start"):
        TemporalWindow(start=datetime(2024, 1, 2, tzinfo=UTC), end=datetime(2024, 1, 1, tzinfo=UTC))


# --- TemporalWindow: immutability / equality ---


def test_window_is_frozen():
    window = TemporalWindow(
        start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 1, 2, tzinfo=UTC)
    )
    with pytest.raises(FrozenInstanceError):
        window.start = datetime(2024, 1, 1, 12, tzinfo=UTC)


def test_window_equality_is_value_based():
    a = TemporalWindow(start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 1, 2, tzinfo=UTC))
    b = TemporalWindow(start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 1, 2, tzinfo=UTC))
    assert a == b


# --- TemporalSplit: valid construction ---


def test_adjacent_is_oos_is_legal():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")
    assert split.in_sample == is_window
    assert split.out_of_sample == oos_window
    assert split.timeframe == "1h"


def test_positive_gap_is_oos_is_legal():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")
    assert split.in_sample.end < split.out_of_sample.start


def test_4h_aligned_split_is_legal():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC)
    )
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="4h")
    assert split.timeframe == "4h"


# --- TemporalSplit: invalid construction ---


def test_overlap_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 5, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="overlap"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_reverse_chronology_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="overlap"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_invalid_in_sample_type_is_rejected():
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(TypeError, match="in_sample"):
        TemporalSplit(in_sample="bad", out_of_sample=oos_window, timeframe="1h")


def test_invalid_out_of_sample_type_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    with pytest.raises(TypeError, match="out_of_sample"):
        TemporalSplit(in_sample=is_window, out_of_sample="bad", timeframe="1h")


def test_misaligned_in_sample_start_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 30, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="in_sample.start"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_misaligned_in_sample_end_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 30, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="in_sample.end"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_misaligned_out_of_sample_start_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 15, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="out_of_sample.start"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_misaligned_out_of_sample_end_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 45, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="out_of_sample.end"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")


def test_unsupported_timeframe_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1d")


def test_uppercase_timeframe_is_not_normalized_and_is_rejected():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1H")


# --- TemporalSplit: immutability ---


def test_split_is_frozen():
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    )
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")
    with pytest.raises(FrozenInstanceError):
        split.timeframe = "4h"


# --- TemporalSplit: alignment is instant-based, not local-clock-based ---


def test_split_alignment_is_based_on_utc_instant_not_local_clock():
    plus_three = timezone(timedelta(hours=3))
    # 03:00 +03:00 == 00:00 UTC and 13:00 +03:00 == 10:00 UTC, both 1h-grid-aligned
    is_window = TemporalWindow(
        start=datetime(2024, 1, 1, 3, 0, tzinfo=plus_three),
        end=datetime(2024, 1, 1, 13, 0, tzinfo=plus_three),
    )
    oos_window = TemporalWindow(
        start=datetime(2024, 1, 1, 13, 0, tzinfo=plus_three),
        end=datetime(2024, 1, 1, 23, 0, tzinfo=plus_three),
    )
    split = TemporalSplit(in_sample=is_window, out_of_sample=oos_window, timeframe="1h")
    assert split.in_sample.start.tzinfo is plus_three  # not silently normalized
