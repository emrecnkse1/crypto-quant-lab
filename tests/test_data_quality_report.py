from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from crypto_quant_lab.data_quality.report import DataQualityReport

_NON_NEGATIVE_COUNT_FIELDS = (
    "expected_count",
    "actual_count",
    "missing_count",
    "unaligned_count",
    "incomplete_tail_excluded_count",
)


def _base_kwargs(**overrides):
    kwargs = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "requested_start": datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        "requested_end": datetime(2024, 1, 1, 15, 0, tzinfo=UTC),
        "as_of_time": datetime(2024, 1, 1, 14, 37, tzinfo=UTC),
        "effective_end": datetime(2024, 1, 1, 14, 0, tzinfo=UTC),
        "first_observed_open_time": datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        "last_observed_open_time": datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
        "expected_count": 4,
        "actual_count": 4,
        "missing_count": 0,
        "missing_samples": (),
        "unaligned_count": 0,
        "unaligned_samples": (),
        "incomplete_tail_excluded_count": 1,
        "overall_status": "PASS",
    }
    kwargs.update(overrides)
    return kwargs


def test_valid_pass_report_constructs():
    report = DataQualityReport(**_base_kwargs(overall_status="PASS"))
    assert report.overall_status == "PASS"


def test_valid_fail_report_constructs():
    report = DataQualityReport(**_base_kwargs(overall_status="FAIL", missing_count=1))
    assert report.overall_status == "FAIL"


def test_all_fields_are_preserved_exactly():
    kwargs = _base_kwargs(
        exchange="binance",
        market_type="spot",
        symbol="ETHUSDT",
        timeframe="4h",
        requested_start=datetime(2024, 3, 1, 8, 0, tzinfo=UTC),
        requested_end=datetime(2024, 3, 1, 20, 0, tzinfo=UTC),
        as_of_time=datetime(2024, 3, 1, 17, 5, tzinfo=UTC),
        effective_end=datetime(2024, 3, 1, 16, 0, tzinfo=UTC),
        first_observed_open_time=datetime(2024, 3, 1, 8, 0, tzinfo=UTC),
        last_observed_open_time=datetime(2024, 3, 1, 12, 0, tzinfo=UTC),
        expected_count=3,
        actual_count=2,
        missing_count=1,
        missing_samples=(datetime(2024, 3, 1, 12, 0, tzinfo=UTC),),
        unaligned_count=1,
        unaligned_samples=(datetime(2024, 3, 1, 9, 30, tzinfo=UTC),),
        incomplete_tail_excluded_count=1,
        overall_status="FAIL",
    )

    report = DataQualityReport(**kwargs)

    for field_name, value in kwargs.items():
        assert getattr(report, field_name) == value


def test_null_observed_boundaries_are_accepted():
    report = DataQualityReport(
        **_base_kwargs(first_observed_open_time=None, last_observed_open_time=None)
    )
    assert report.first_observed_open_time is None
    assert report.last_observed_open_time is None


def test_report_is_frozen():
    report = DataQualityReport(**_base_kwargs())
    with pytest.raises(FrozenInstanceError):
        report.overall_status = "FAIL"


def test_missing_samples_tuple_is_accepted():
    report = DataQualityReport(
        **_base_kwargs(missing_count=1, missing_samples=(datetime(2024, 1, 1, 11, 0, tzinfo=UTC),))
    )
    assert report.missing_samples == (datetime(2024, 1, 1, 11, 0, tzinfo=UTC),)


def test_unaligned_samples_tuple_is_accepted():
    report = DataQualityReport(
        **_base_kwargs(
            unaligned_count=1, unaligned_samples=(datetime(2024, 1, 1, 11, 30, tzinfo=UTC),)
        )
    )
    assert report.unaligned_samples == (datetime(2024, 1, 1, 11, 30, tzinfo=UTC),)


def test_missing_samples_list_is_rejected_no_silent_conversion():
    with pytest.raises(TypeError, match="missing_samples"):
        DataQualityReport(
            **_base_kwargs(
                missing_count=1, missing_samples=[datetime(2024, 1, 1, 11, 0, tzinfo=UTC)]
            )
        )


def test_unaligned_samples_list_is_rejected_no_silent_conversion():
    with pytest.raises(TypeError, match="unaligned_samples"):
        DataQualityReport(
            **_base_kwargs(
                unaligned_count=1, unaligned_samples=[datetime(2024, 1, 1, 11, 30, tzinfo=UTC)]
            )
        )


def test_missing_samples_at_cap_of_20_is_accepted():
    samples = tuple(datetime(2024, 1, 1, hour, 0, tzinfo=UTC) for hour in range(20))
    report = DataQualityReport(**_base_kwargs(missing_count=20, missing_samples=samples))
    assert len(report.missing_samples) == 20


def test_missing_samples_over_cap_of_20_is_rejected():
    samples = tuple(datetime(2024, 1, 1, hour % 24, 0, tzinfo=UTC) for hour in range(21))
    with pytest.raises(ValueError, match="missing_samples"):
        DataQualityReport(**_base_kwargs(missing_count=21, missing_samples=samples))


def test_unaligned_samples_at_cap_of_20_is_accepted():
    samples = tuple(datetime(2024, 1, 1, hour, 0, tzinfo=UTC) for hour in range(20))
    report = DataQualityReport(**_base_kwargs(unaligned_count=20, unaligned_samples=samples))
    assert len(report.unaligned_samples) == 20


def test_unaligned_samples_over_cap_of_20_is_rejected():
    samples = tuple(datetime(2024, 1, 1, hour % 24, 0, tzinfo=UTC) for hour in range(21))
    with pytest.raises(ValueError, match="unaligned_samples"):
        DataQualityReport(**_base_kwargs(unaligned_count=21, unaligned_samples=samples))


@pytest.mark.parametrize("invalid_status", ["pass", "fail", "OK", "UNKNOWN", ""])
def test_invalid_overall_status_is_rejected(invalid_status):
    with pytest.raises(ValueError, match="overall_status"):
        DataQualityReport(**_base_kwargs(overall_status=invalid_status))


@pytest.mark.parametrize("field_name", _NON_NEGATIVE_COUNT_FIELDS)
def test_negative_count_field_is_rejected(field_name):
    with pytest.raises(ValueError, match=field_name):
        DataQualityReport(**_base_kwargs(**{field_name: -1}))


def test_zero_count_values_are_accepted():
    report = DataQualityReport(
        **_base_kwargs(
            expected_count=0,
            actual_count=0,
            missing_count=0,
            unaligned_count=0,
            incomplete_tail_excluded_count=0,
        )
    )
    assert report.expected_count == 0
    assert report.incomplete_tail_excluded_count == 0


def test_naive_datetime_is_not_defensively_rejected():
    """Locks the contract: the model trusts upstream tz-awareness guarantees."""
    naive_requested_start = datetime(2024, 1, 1, 10, 0)  # noqa: DTZ001
    report = DataQualityReport(**_base_kwargs(requested_start=naive_requested_start))
    assert report.requested_start == naive_requested_start


def test_observed_boundary_ordering_is_not_enforced():
    """Locks the contract: no first_observed <= last_observed invariant at this layer."""
    report = DataQualityReport(
        **_base_kwargs(
            first_observed_open_time=datetime(2024, 1, 1, 13, 0, tzinfo=UTC),
            last_observed_open_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
        )
    )
    assert report.first_observed_open_time > report.last_observed_open_time


def test_arithmetically_inconsistent_counts_are_not_reconciled():
    """Locks the contract: count relationships are not validated at this layer."""
    report = DataQualityReport(
        **_base_kwargs(
            expected_count=10,
            actual_count=50,
            missing_count=7,
            unaligned_count=40,
        )
    )
    assert report.expected_count == 10
    assert report.actual_count == 50
    assert report.missing_count == 7
    assert report.unaligned_count == 40
