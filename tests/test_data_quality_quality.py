from datetime import UTC, datetime, timedelta, timezone

import pytest

from crypto_quant_lab.data_quality.quality import build_data_quality_report


def _perfect_1h_kwargs(**overrides):
    kwargs = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "requested_start": datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        "requested_end": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "as_of_time": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "effective_end": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "observed_open_times": (
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        ),
    }
    kwargs.update(overrides)
    return kwargs


# --- perfect data ---


def test_perfect_1h_dataset_passes():
    report = build_data_quality_report(**_perfect_1h_kwargs())

    assert report.expected_count == 4
    assert report.actual_count == 4
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.incomplete_tail_excluded_count == 0
    assert report.overall_status == "PASS"


def test_perfect_4h_dataset_passes():
    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="4h",
        requested_start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 20, 0, tzinfo=UTC),
        observed_open_times=(
            datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
        ),
    )

    assert report.expected_count == 3
    assert report.actual_count == 3
    assert report.missing_count == 0
    assert report.overall_status == "PASS"


# --- missing ---


def test_single_missing_timestamp_fails():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            )
        )
    )

    assert report.missing_count == 1
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)
    assert report.overall_status == "FAIL"


def test_multiple_missing_timestamps_are_chronological():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            )
        )
    )

    assert report.missing_count == 2
    assert report.missing_samples == (
        datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert report.overall_status == "FAIL"


def test_exactly_20_missing_is_not_truncated():
    requested_start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    effective_end = requested_start + timedelta(hours=20)
    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=requested_start,
        requested_end=effective_end,
        as_of_time=effective_end,
        effective_end=effective_end,
        observed_open_times=(),
    )

    assert report.expected_count == 20
    assert report.missing_count == 20
    assert len(report.missing_samples) == 20
    assert report.missing_samples[0] == requested_start
    assert report.missing_samples[-1] == requested_start + timedelta(hours=19)


def test_more_than_20_missing_is_capped_at_20_samples():
    requested_start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    effective_end = requested_start + timedelta(hours=21)
    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=requested_start,
        requested_end=effective_end,
        as_of_time=effective_end,
        effective_end=effective_end,
        observed_open_times=(),
    )

    assert report.expected_count == 21
    assert report.missing_count == 21
    assert len(report.missing_samples) == 20
    assert report.missing_samples[-1] == requested_start + timedelta(hours=19)


# --- unaligned ---


def test_single_extra_unaligned_row_fails():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
                datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            )
        )
    )

    assert report.actual_count == 5
    assert report.missing_count == 0
    assert report.unaligned_count == 1
    assert report.unaligned_samples == (datetime(2024, 1, 1, 11, 30, tzinfo=UTC),)
    assert report.overall_status == "FAIL"


def test_expected_slot_replaced_by_unaligned_row_fails():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
                datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            )
        )
    )

    assert report.expected_count == 4
    assert report.actual_count == 4
    assert report.missing_count == 1
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)
    assert report.unaligned_count == 1
    assert report.unaligned_samples == (datetime(2024, 1, 1, 11, 30, tzinfo=UTC),)
    assert report.overall_status == "FAIL"


def test_all_expected_rows_plus_one_unaligned_fails():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
                datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            )
        )
    )

    assert report.actual_count > report.expected_count
    assert report.missing_count == 0
    assert report.unaligned_count == 1
    assert report.overall_status == "FAIL"


def test_more_than_20_unaligned_is_capped_at_20_samples():
    aligned = (datetime(2024, 1, 1, 10, 0, tzinfo=UTC),)
    unaligned = tuple(
        datetime(2024, 1, 1, 10, minute, tzinfo=UTC) for minute in range(1, 22)
    )  # 21 unaligned rows: 10:01..10:21
    observed = aligned + unaligned

    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
        observed_open_times=observed,
    )

    assert report.missing_count == 0
    assert report.unaligned_count == 21
    assert len(report.unaligned_samples) == 20
    assert report.unaligned_samples[-1] == datetime(2024, 1, 1, 10, 20, tzinfo=UTC)
    assert report.overall_status == "FAIL"


# --- empty / tail ---


def test_empty_observed_dataset_fails():
    report = build_data_quality_report(**_perfect_1h_kwargs(observed_open_times=()))

    assert report.first_observed_open_time is None
    assert report.last_observed_open_time is None
    assert report.actual_count == 0
    assert report.missing_count == report.expected_count
    assert report.overall_status == "FAIL"


def test_incomplete_tail_alone_passes():
    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        requested_end=datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        effective_end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        observed_open_times=(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        ),
    )

    assert report.incomplete_tail_excluded_count == 1
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.overall_status == "PASS"


# --- boundaries ---


def test_first_and_last_observed_are_exact():
    report = build_data_quality_report(**_perfect_1h_kwargs())

    assert report.first_observed_open_time == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert report.last_observed_open_time == datetime(2024, 1, 1, 13, 0, tzinfo=UTC)


def test_requested_start_equal_effective_end_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                effective_end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                observed_open_times=(),
            )
        )


def test_requested_start_after_effective_end_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                effective_end=datetime(2024, 1, 1, 9, 0, tzinfo=UTC),
                observed_open_times=(),
            )
        )


def test_effective_end_after_requested_end_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                requested_end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
                observed_open_times=(),
            )
        )


@pytest.mark.parametrize(
    "field_name",
    ["requested_start", "requested_end", "effective_end"],
)
def test_unaligned_boundary_datetime_is_rejected(field_name):
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                **{field_name: datetime(2024, 1, 1, 10, 30, tzinfo=UTC)},
                observed_open_times=(),
            )
        )


def test_unsupported_timeframe_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(**_perfect_1h_kwargs(timeframe="15m", observed_open_times=()))


# --- observed range ---


def test_observed_before_requested_start_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(observed_open_times=(datetime(2024, 1, 1, 9, 0, tzinfo=UTC),))
        )


def test_observed_exactly_at_effective_end_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(observed_open_times=(datetime(2024, 1, 1, 14, 0, tzinfo=UTC),))
        )


def test_observed_after_effective_end_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(observed_open_times=(datetime(2024, 1, 1, 15, 0, tzinfo=UTC),))
        )


# --- observed order ---


def test_duplicate_observed_timestamp_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                observed_open_times=(
                    datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                    datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                )
            )
        )


def test_same_instant_different_offset_is_rejected_as_duplicate():
    plus_two = timezone(timedelta(hours=2))
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                observed_open_times=(
                    datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                    datetime(2024, 1, 1, 12, 0, tzinfo=plus_two),  # == 10:00 UTC
                )
            )
        )


def test_backward_observed_timestamp_is_rejected():
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(
                observed_open_times=(
                    datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                    datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                )
            )
        )


# --- timezone ---


def test_non_utc_aware_equivalent_instants_produce_same_semantics():
    plus_five = timezone(timedelta(hours=5))
    report = build_data_quality_report(
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start=datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),  # == 10:00 UTC
        requested_end=datetime(2024, 1, 1, 19, 0, tzinfo=plus_five),  # == 14:00 UTC
        as_of_time=datetime(2024, 1, 1, 19, 0, tzinfo=plus_five),
        effective_end=datetime(2024, 1, 1, 19, 0, tzinfo=plus_five),
        observed_open_times=(
            datetime(2024, 1, 1, 15, 0, tzinfo=plus_five),
            datetime(2024, 1, 1, 16, 0, tzinfo=plus_five),
            datetime(2024, 1, 1, 17, 0, tzinfo=plus_five),
            datetime(2024, 1, 1, 18, 0, tzinfo=plus_five),
        ),
    )

    assert report.expected_count == 4
    assert report.actual_count == 4
    assert report.missing_count == 0
    assert report.unaligned_count == 0
    assert report.overall_status == "PASS"


@pytest.mark.parametrize(
    "field_name",
    ["requested_start", "requested_end", "as_of_time", "effective_end"],
)
def test_naive_boundary_datetime_is_rejected(field_name):
    naive_value = datetime(2024, 1, 1, 10, 0)  # noqa: DTZ001
    with pytest.raises(ValueError):
        build_data_quality_report(
            **_perfect_1h_kwargs(**{field_name: naive_value}, observed_open_times=())
        )


def test_naive_observed_datetime_is_rejected():
    naive_value = datetime(2024, 1, 1, 10, 0)  # noqa: DTZ001
    with pytest.raises(ValueError):
        build_data_quality_report(**_perfect_1h_kwargs(observed_open_times=(naive_value,)))


def test_as_of_time_may_be_arbitrary_non_grid_aligned():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(as_of_time=datetime(2024, 1, 1, 14, 37, 12, 345678, tzinfo=UTC))
    )
    assert report.overall_status == "PASS"


# --- immutability / no repair ---


def test_observed_input_sequence_is_not_mutated():
    observed = [
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    ]
    original_copy = list(observed)

    build_data_quality_report(**_perfect_1h_kwargs(observed_open_times=observed))

    assert observed == original_copy


def test_missing_timestamp_is_reported_but_not_materialized_as_observed():
    observed = (
        datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
    )

    report = build_data_quality_report(**_perfect_1h_kwargs(observed_open_times=observed))

    assert report.actual_count == len(observed)
    assert datetime(2024, 1, 1, 11, 0, tzinfo=UTC) in report.missing_samples
    assert datetime(2024, 1, 1, 11, 0, tzinfo=UTC) not in observed


def test_samples_are_deterministic_across_repeated_calls():
    kwargs = _perfect_1h_kwargs(
        observed_open_times=(
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        )
    )

    first = build_data_quality_report(**kwargs)
    second = build_data_quality_report(**kwargs)

    assert first.missing_samples == second.missing_samples
    assert first.unaligned_samples == second.unaligned_samples
    assert first.overall_status == second.overall_status


# --- no expected-actual shortcut regression ---


def test_missing_and_unaligned_counts_never_use_expected_minus_actual_shortcut():
    report = build_data_quality_report(
        **_perfect_1h_kwargs(
            observed_open_times=(
                datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                datetime(2024, 1, 1, 11, 30, tzinfo=UTC),
                datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            )
        )
    )

    assert report.expected_count == 4
    assert report.actual_count == 3
    assert report.missing_count == 2  # 11:00 and 13:00
    assert report.unaligned_count == 1  # 11:30
    assert report.overall_status == "FAIL"
    # the wrong shortcut (expected_count - actual_count == 1) must not equal missing_count
    assert report.expected_count - report.actual_count != report.missing_count
