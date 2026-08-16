from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.models import (
    FundingCoverageInterval,
    FundingEvent,
    HistoricalFundingEvent,
)
from crypto_quant_lab.funding.quality import (
    FundingDataQualityReport,
    build_funding_data_quality_report,
    build_funding_data_quality_report_from_store,
)
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore

START = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
END = datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC)  # START + 24h

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"


def _h(hours: float) -> datetime:
    return START + timedelta(hours=hours)


def _coverage(start: datetime, end: datetime) -> FundingCoverageInterval:
    return FundingCoverageInterval(start_time=start, end_time=end)


def _event(
    event_time: datetime,
    rate_type: str = "Regular",
    *,
    exchange: str = EXCHANGE,
    market_type: str = MARKET_TYPE,
    symbol: str = SYMBOL,
    funding_rate: str = "0.001",
    reference_price: str = "100.50",
) -> HistoricalFundingEvent:
    return HistoricalFundingEvent(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        funding=FundingEvent(
            event_time=event_time,
            funding_rate=Decimal(funding_rate),
            reference_price=Decimal(reference_price),
            rate_type=rate_type,
        ),
    )


def _build(events=(), coverage_intervals=(), *, requested_start=START, requested_end=END):
    return build_funding_data_quality_report(
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        requested_start=requested_start,
        requested_end=requested_end,
        events=events,
        coverage_intervals=coverage_intervals,
    )


class _FakeStore:
    def __init__(self, *, events=(), coverage=(), raise_exc=None, raise_on=None):
        self._events = events
        self._coverage = coverage
        self.event_calls: list[dict] = []
        self.coverage_calls: list[dict] = []
        self._raise_exc = raise_exc
        self._raise_on = raise_on

    def query_events(self, *, exchange, market_type, symbol, start_time, end_time):
        self.event_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        if self._raise_exc is not None and self._raise_on == "events":
            raise self._raise_exc
        return self._events

    def query_coverage(self, *, exchange, market_type, symbol, start_time, end_time):
        self.coverage_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        if self._raise_exc is not None and self._raise_on == "coverage":
            raise self._raise_exc
        return self._coverage

    def write_ingestion_batch(self, *args, **kwargs):
        raise AssertionError("quality builder must never write")

    def close(self):
        pass


# --- report model ---


def test_report_is_frozen():
    report = _build([], [_coverage(START, END)])
    with pytest.raises(AttributeError):
        report.overall_status = "FAIL"  # type: ignore[misc]


def test_report_rejects_invalid_status():
    with pytest.raises(ValueError, match="overall_status"):
        FundingDataQualityReport(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
            event_count=0,
            coverage_gap_count=0,
            coverage_gaps=(),
            overall_status="MAYBE",
        )


def test_report_rejects_negative_counts():
    with pytest.raises(ValueError, match="event_count"):
        FundingDataQualityReport(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
            event_count=-1,
            coverage_gap_count=0,
            coverage_gaps=(),
            overall_status="PASS",
        )


def test_report_rejects_bool_counts():
    with pytest.raises(TypeError, match="event_count"):
        FundingDataQualityReport(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
            event_count=True,
            coverage_gap_count=0,
            coverage_gaps=(),
            overall_status="PASS",
        )


def test_report_rejects_gaps_tuple_over_max_samples():
    too_many = tuple((_h(i), _h(i + 0.5)) for i in range(21))
    with pytest.raises(ValueError, match="coverage_gaps"):
        FundingDataQualityReport(
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
            event_count=0,
            coverage_gap_count=21,
            coverage_gaps=too_many,
            overall_status="FAIL",
        )


def test_report_gaps_are_deterministic_tuple():
    report = _build([], [])
    assert isinstance(report.coverage_gaps, tuple)
    assert report.coverage_gaps == ((START, END),)


# --- basic PASS/FAIL ---


def test_full_coverage_pass():
    report = _build([], [_coverage(START, END)])

    assert report.overall_status == "PASS"
    assert report.coverage_gap_count == 0
    assert report.coverage_gaps == ()


def test_zero_event_pass():
    report = _build([], [_coverage(START, END)])

    assert report.overall_status == "PASS"
    assert report.event_count == 0


def test_no_coverage_fail():
    report = _build([], [])

    assert report.overall_status == "FAIL"
    assert report.event_count == 0
    assert report.coverage_gap_count == 1
    assert report.coverage_gaps == ((START, END),)


def test_head_gap_fail():
    report = _build([], [_coverage(_h(4), END)])

    assert report.overall_status == "FAIL"
    assert report.coverage_gaps == ((START, _h(4)),)


def test_tail_gap_fail():
    report = _build([], [_coverage(START, _h(20))])

    assert report.overall_status == "FAIL"
    assert report.coverage_gaps == ((_h(20), END),)


def test_internal_gap_fail():
    report = _build([], [_coverage(START, _h(8)), _coverage(_h(12), END)])

    assert report.overall_status == "FAIL"
    assert report.coverage_gaps == ((_h(8), _h(12)),)


def test_adjacent_intervals_pass():
    report = _build([], [_coverage(START, _h(12)), _coverage(_h(12), END)])

    assert report.overall_status == "PASS"
    assert report.coverage_gap_count == 0


def test_overlapping_intervals_pass():
    report = _build([], [_coverage(START, _h(16)), _coverage(_h(8), END)])

    assert report.overall_status == "PASS"


def test_superset_coverage_pass():
    report = _build([], [_coverage(START, END)], requested_start=_h(8), requested_end=_h(20))

    assert report.overall_status == "PASS"


def test_nested_coverage_pass():
    report = _build([], [_coverage(START, END), _coverage(_h(4), _h(8))])

    assert report.overall_status == "PASS"
    assert report.coverage_gap_count == 0


def test_complex_union_pass():
    coverage = [
        _coverage(_h(0), _h(5)),
        _coverage(_h(4), _h(10)),
        _coverage(_h(10), _h(15)),
        _coverage(_h(14), _h(24)),
    ]
    report = _build([], coverage)

    assert report.overall_status == "PASS"


def test_one_microsecond_gap_fail():
    just_after_noon = _h(12) + timedelta(microseconds=1)
    report = _build([], [_coverage(START, _h(12)), _coverage(just_after_noon, END)])

    assert report.overall_status == "FAIL"
    assert report.coverage_gaps == ((_h(12), just_after_noon),)


def test_gap_sample_cap():
    requested_end = START + timedelta(hours=50)
    coverage = [_coverage(_h(2 * i), _h(2 * i + 1)) for i in range(25)]

    report = _build([], coverage, requested_end=requested_end)

    assert report.overall_status == "FAIL"
    assert report.coverage_gap_count == 25
    assert len(report.coverage_gaps) == 20
    assert report.coverage_gaps[0] == (_h(1), _h(2))
    assert report.coverage_gaps[19] == (_h(39), _h(40))


# --- event count informational ---


def test_zero_one_and_several_irregular_events_all_pass():
    coverage = [_coverage(START, END)]

    assert _build([], coverage).overall_status == "PASS"

    one_event = [_event(_h(3))]
    report_one = _build(one_event, coverage)
    assert report_one.overall_status == "PASS"
    assert report_one.event_count == 1

    irregular = [_event(_h(0.1)), _event(_h(1)), _event(_h(23.9))]
    report_many = _build(irregular, coverage)
    assert report_many.overall_status == "PASS"
    assert report_many.event_count == 3


def test_same_time_regular_and_special_pass_no_merge():
    t = _h(8)
    events = [_event(t, "Regular"), _event(t, "Special")]
    report = _build(events, [_coverage(START, END)])

    assert report.overall_status == "PASS"
    assert report.event_count == 2


def test_opaque_rate_type_is_accepted():
    events = [_event(_h(8), "SomeOtherExchangeType")]
    report = _build(events, [_coverage(START, END)])

    assert report.overall_status == "PASS"


# --- event contract violations (exceptions, not FAIL) ---


def test_event_order_violation_raises():
    events = [_event(_h(10)), _event(_h(5))]
    with pytest.raises(ValueError, match="not ordered"):
        _build(events, [_coverage(START, END)])


def test_same_time_rate_type_order_violation_raises():
    t = _h(8)
    events = [_event(t, "Special"), _event(t, "Regular")]
    with pytest.raises(ValueError, match="not ordered"):
        _build(events, [_coverage(START, END)])


def test_duplicate_canonical_key_raises():
    t = _h(8)
    events = [_event(t, "Regular"), _event(t, "Regular")]
    with pytest.raises(ValueError, match="duplicate canonical event key"):
        _build(events, [_coverage(START, END)])


@pytest.mark.parametrize(
    "bad_kwargs", [{"exchange": "bybit"}, {"market_type": "spot"}, {"symbol": "ETHUSDT"}]
)
def test_partition_mismatch_raises(bad_kwargs):
    events = [_event(_h(8), **bad_kwargs)]
    with pytest.raises(ValueError, match="partition"):
        _build(events, [_coverage(START, END)])


def test_out_of_range_event_before_start_raises():
    events = [_event(START - timedelta(hours=1))]
    with pytest.raises(ValueError, match="outside the requested"):
        _build(events, [_coverage(START, END)])


def test_out_of_range_event_at_end_raises():
    events = [_event(END)]
    with pytest.raises(ValueError, match="outside the requested"):
        _build(events, [_coverage(START, END)])


def test_wrong_event_object_type_raises():
    with pytest.raises(TypeError, match="HistoricalFundingEvent"):
        _build([{"not": "an event"}], [_coverage(START, END)])


# --- coverage contract violations (exceptions, not FAIL) ---


def test_bad_coverage_object_raises():
    with pytest.raises(TypeError, match="FundingCoverageInterval"):
        _build([], [("not", "an interval")])


def test_non_overlapping_coverage_raises():
    far_away = _coverage(START + timedelta(days=10), START + timedelta(days=11))
    with pytest.raises(ValueError, match="does not overlap"):
        _build([], [far_away])


def test_coverage_order_violation_later_then_earlier_start_raises():
    coverage = [_coverage(_h(12), END), _coverage(START, _h(6))]
    with pytest.raises(ValueError, match="not ordered"):
        _build([], coverage)


def test_coverage_order_violation_same_start_decreasing_end_raises():
    coverage = [_coverage(START, _h(12)), _coverage(START, _h(8))]
    with pytest.raises(ValueError, match="not ordered"):
        _build([], coverage)


def test_coverage_legal_nested_order_does_not_raise():
    coverage = [_coverage(START, END), _coverage(_h(4), _h(8))]

    report = _build([], coverage)

    assert report.overall_status == "PASS"


# --- store-backed builder ---


def test_store_backed_builder_calls_query_events_and_query_coverage_with_exact_args():
    events = [_event(_h(8))]
    coverage = [_coverage(START, END)]
    store = _FakeStore(events=events, coverage=coverage)

    report = build_funding_data_quality_report_from_store(
        store,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        requested_start=START,
        requested_end=END,
    )

    assert len(store.event_calls) == 1
    assert store.event_calls[0] == {
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "start_time": START,
        "end_time": END,
    }
    assert len(store.coverage_calls) == 1
    assert store.coverage_calls[0] == {
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "start_time": START,
        "end_time": END,
    }
    assert report.overall_status == "PASS"
    assert report.event_count == 1
    assert report == _build(events, coverage)


def test_store_query_error_propagates_not_converted_to_fail():
    class _Sentinel(Exception):
        pass

    store = _FakeStore(raise_exc=_Sentinel("query broke"), raise_on="events")

    with pytest.raises(_Sentinel, match="query broke"):
        build_funding_data_quality_report_from_store(
            store,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
        )


# --- real SQLite regressions ---


def test_sqlite_pass_e2e(tmp_path):
    store = SQLiteHistoricalFundingStore(tmp_path / "funding.db")
    try:
        events = [_event(_h(8), "Regular"), _event(_h(16), "Regular"), _event(_h(16), "Special")]
        store.write_ingestion_batch(
            events,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            covered_start=START,
            covered_end=END,
        )

        report = build_funding_data_quality_report_from_store(
            store,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
        )

        assert report.overall_status == "PASS"
        assert report.event_count == 3
        assert report.coverage_gap_count == 0
    finally:
        store.close()


def test_sqlite_zero_event_pass_e2e(tmp_path):
    store = SQLiteHistoricalFundingStore(tmp_path / "funding.db")
    try:
        store.write_ingestion_batch(
            [],
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            covered_start=START,
            covered_end=END,
        )

        report = build_funding_data_quality_report_from_store(
            store,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
        )

        assert report.overall_status == "PASS"
        assert report.event_count == 0
    finally:
        store.close()


def test_sqlite_partial_coverage_fail_e2e(tmp_path):
    store = SQLiteHistoricalFundingStore(tmp_path / "funding.db")
    try:
        store.write_ingestion_batch(
            [],
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            covered_start=START,
            covered_end=_h(12),
        )

        report = build_funding_data_quality_report_from_store(
            store,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            requested_start=START,
            requested_end=END,
        )

        assert report.overall_status == "FAIL"
        assert report.coverage_gaps == ((_h(12), END),)
    finally:
        store.close()
