from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.binance import BinanceHistoricalFundingRecord
from crypto_quant_lab.funding.ingestion import ingest_binance_historical_funding_range
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

START = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
END = datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC)
INGESTION_AS_OF = datetime(2024, 1, 2, 1, 0, 0, tzinfo=UTC)


class _PseudoNaiveTzInfo(tzinfo):
    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


class _FakeStore:
    def __init__(self, *, raise_exc: BaseException | None = None):
        self.calls: list[dict] = []
        self._raise_exc = raise_exc

    def write_ingestion_batch(
        self, events, *, exchange, market_type, symbol, covered_start, covered_end
    ):
        self.calls.append(
            {
                "events": list(events),
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "covered_start": covered_start,
                "covered_end": covered_end,
            }
        )
        if self._raise_exc is not None:
            raise self._raise_exc


def _record(
    event_time: datetime,
    rate_type: str = "Regular",
    *,
    symbol: str = "BTCUSDT",
    funding_rate: str = "0.001",
    mark_price: str = "100.50",
) -> BinanceHistoricalFundingRecord:
    return BinanceHistoricalFundingRecord(
        symbol=symbol,
        funding_rate=Decimal(funding_rate),
        funding_time=event_time,
        mark_price=Decimal(mark_price),
        rate_type=rate_type,
    )


def _make_fetch_history(*outcomes: object):
    calls: list[dict] = []
    outcomes_iter = iter(outcomes)

    def _fetch(symbol, start_time_ms, end_time_ms, *, timeout, max_attempts):
        calls.append(
            {
                "symbol": symbol,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "timeout": timeout,
                "max_attempts": max_attempts,
            }
        )
        outcome = next(outcomes_iter)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    _fetch.calls = calls
    return _fetch


def _ingest(
    store,
    *,
    fetch_history,
    symbol="BTCUSDT",
    market_type="usdm_perp",
    requested_start=START,
    requested_end=END,
    source_as_of=END,
    ingestion_as_of=INGESTION_AS_OF,
    **kwargs,
):
    return ingest_binance_historical_funding_range(
        store,
        symbol=symbol,
        market_type=market_type,
        requested_start=requested_start,
        requested_end=requested_end,
        source_as_of=source_as_of,
        ingestion_as_of=ingestion_as_of,
        fetch_history=fetch_history,
        **kwargs,
    )


# --- basic mapping ---


def test_basic_mapping_maps_all_fields_exactly():
    t = START + timedelta(hours=8)
    raw = _record(t, "Regular", funding_rate="0.001", mark_price="100.50")
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch, market_type="usdm_perp")

    assert len(result) == 1
    event = result[0]
    assert event.exchange == "binance"
    assert event.market_type == "usdm_perp"
    assert event.symbol == "BTCUSDT"
    assert event.funding.event_time == t
    assert event.funding.funding_rate == Decimal("0.001")
    assert event.funding.reference_price == Decimal("100.50")
    assert event.funding.rate_type == "Regular"


def test_same_time_regular_and_special_both_mapped():
    t = START + timedelta(hours=8)
    regular = _record(t, "Regular")
    special = _record(t, "Special")
    store = _FakeStore()
    fetch = _make_fetch_history([regular, special])

    result = _ingest(store, fetch_history=fetch)

    assert len(result) == 2
    assert result[0].funding.rate_type == "Regular"
    assert result[1].funding.rate_type == "Special"


def test_negative_and_zero_rate_preserved():
    t1 = START + timedelta(hours=8)
    t2 = START + timedelta(hours=16)
    neg = _record(t1, funding_rate="-0.0005")
    zero = _record(t2, funding_rate="0")
    store = _FakeStore()
    fetch = _make_fetch_history([neg, zero])

    result = _ingest(store, fetch_history=fetch)

    assert result[0].funding.funding_rate == Decimal("-0.0005")
    assert result[1].funding.funding_rate == Decimal(0)


def test_decimal_exactness_preserved():
    t = START + timedelta(hours=8)
    raw = _record(t, funding_rate="0.000123456789", mark_price="65000.123456789012")
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert result[0].funding.funding_rate == Decimal("0.000123456789")
    assert result[0].funding.reference_price == Decimal("65000.123456789012")


# --- half-open filtering ---


def test_event_at_requested_start_is_kept():
    raw = _record(START)
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert len(result) == 1


def test_event_at_requested_end_is_dropped_not_error():
    raw = _record(END)
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert result == []
    assert len(store.calls) == 1


def test_event_before_requested_start_is_dropped():
    before = START - timedelta(milliseconds=1)
    raw = _record(before)
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert result == []


def test_event_after_requested_end_is_dropped_not_source_as_of_error():
    after = END + timedelta(milliseconds=1)
    raw = _record(after)
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert result == []
    assert len(store.calls) == 1


def test_event_strictly_within_range_is_kept():
    raw = _record(START + timedelta(hours=12))
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch)

    assert len(result) == 1


# --- transport bounds ---


def test_transport_bounds_exact_for_ms_aligned_canonical_range():
    store = _FakeStore()
    fetch = _make_fetch_history([])

    _ingest(store, fetch_history=fetch)

    expected_start_ms = datetime_to_epoch_us(START) // 1000
    expected_end_ms = datetime_to_epoch_us(END) // 1000
    assert fetch.calls[0]["start_time_ms"] == expected_start_ms
    assert fetch.calls[0]["end_time_ms"] == expected_end_ms


def test_sub_ms_start_floors_transport_and_filters_overfetch():
    sub_ms_start = START + timedelta(microseconds=500)
    floor_event = _record(START)  # before sub_ms_start canonically
    store = _FakeStore()
    fetch = _make_fetch_history([floor_event])

    result = _ingest(store, fetch_history=fetch, requested_start=sub_ms_start, source_as_of=END)

    expected_start_ms = datetime_to_epoch_us(START) // 1000
    assert fetch.calls[0]["start_time_ms"] == expected_start_ms
    assert result == []


def test_sub_ms_end_ceils_transport_and_retains_boundary_event():
    sub_ms_end = END + timedelta(microseconds=500)
    floor_event = _record(END)  # END < sub_ms_end canonically -> kept
    ceil_overfetch_event = _record(END + timedelta(milliseconds=1))  # > sub_ms_end -> dropped
    store = _FakeStore()
    fetch = _make_fetch_history([floor_event, ceil_overfetch_event])

    result = _ingest(store, fetch_history=fetch, requested_end=sub_ms_end, source_as_of=sub_ms_end)

    expected_end_ms = datetime_to_epoch_us(END) // 1000 + 1
    assert fetch.calls[0]["end_time_ms"] == expected_end_ms
    assert len(result) == 1
    assert result[0].funding.event_time == END


# --- source_as_of ---


def test_source_as_of_before_requested_end_raises_before_fetch():
    store = _FakeStore()
    fetch = _make_fetch_history([])

    with pytest.raises(ValueError, match="source_as_of"):
        _ingest(store, fetch_history=fetch, source_as_of=END - timedelta(seconds=1))

    assert len(fetch.calls) == 0
    assert len(store.calls) == 0


def test_source_as_of_equal_to_requested_end_is_legal():
    raw = _record(START + timedelta(hours=1))
    store = _FakeStore()
    fetch = _make_fetch_history([raw])

    result = _ingest(store, fetch_history=fetch, source_as_of=END)

    assert len(result) == 1
    assert len(store.calls) == 1


# --- ingestion_as_of ---


def test_naive_ingestion_as_of_is_rejected():
    store = _FakeStore()
    fetch = _make_fetch_history([])

    with pytest.raises(ValueError, match="timezone-aware"):
        _ingest(store, fetch_history=fetch, ingestion_as_of=datetime(2024, 1, 2))  # noqa: DTZ001

    assert len(fetch.calls) == 0
    assert len(store.calls) == 0


def test_pseudo_naive_ingestion_as_of_is_rejected():
    store = _FakeStore()
    fetch = _make_fetch_history([])
    pseudo_naive = datetime(2024, 1, 2, tzinfo=_PseudoNaiveTzInfo())

    with pytest.raises(ValueError, match="timezone-aware"):
        _ingest(store, fetch_history=fetch, ingestion_as_of=pseudo_naive)

    assert len(fetch.calls) == 0
    assert len(store.calls) == 0


# --- invalid canonical range ---


def test_start_equal_end_is_rejected():
    with pytest.raises(ValueError, match="requested_start"):
        _ingest(
            _FakeStore(),
            fetch_history=_make_fetch_history([]),
            requested_start=START,
            requested_end=START,
            source_as_of=START,
        )


def test_start_after_end_is_rejected():
    with pytest.raises(ValueError, match="requested_start"):
        _ingest(
            _FakeStore(),
            fetch_history=_make_fetch_history([]),
            requested_start=END,
            requested_end=START,
            source_as_of=END,
        )


def test_naive_requested_start_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _ingest(
            _FakeStore(),
            fetch_history=_make_fetch_history([]),
            requested_start=datetime(2024, 1, 1),  # noqa: DTZ001
        )


# --- invalid metadata ---


@pytest.mark.parametrize("bad_symbol", ["", "   ", 123, None])
def test_invalid_symbol_is_rejected_before_fetch(bad_symbol):
    fetch = _make_fetch_history([])
    with pytest.raises(ValueError, match="symbol"):
        _ingest(_FakeStore(), fetch_history=fetch, symbol=bad_symbol)
    assert len(fetch.calls) == 0


@pytest.mark.parametrize("bad_market_type", ["", "   ", 123, None])
def test_invalid_market_type_is_rejected_before_fetch(bad_market_type):
    fetch = _make_fetch_history([])
    with pytest.raises(ValueError, match="market_type"):
        _ingest(_FakeStore(), fetch_history=fetch, market_type=bad_market_type)
    assert len(fetch.calls) == 0


# --- empty / filtered-to-empty success ---


def test_empty_source_success_writes_coverage_once():
    store = _FakeStore()
    fetch = _make_fetch_history([])

    result = _ingest(store, fetch_history=fetch)

    assert result == []
    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["events"] == []
    assert call["exchange"] == "binance"
    assert call["market_type"] == "usdm_perp"
    assert call["symbol"] == "BTCUSDT"
    assert call["covered_start"] == START
    assert call["covered_end"] == END


def test_filtered_to_empty_success_writes_coverage_once():
    before = _record(START - timedelta(milliseconds=1))
    at_end = _record(END)
    store = _FakeStore()
    fetch = _make_fetch_history([before, at_end])

    result = _ingest(store, fetch_history=fetch)

    assert result == []
    assert len(store.calls) == 1
    assert store.calls[0]["events"] == []


# --- failure paths ---


def test_paginator_connection_error_propagates_zero_store_calls():
    store = _FakeStore()
    fetch = _make_fetch_history(ConnectionError("boom"))

    with pytest.raises(ConnectionError, match="boom"):
        _ingest(store, fetch_history=fetch)

    assert len(store.calls) == 0


def test_paginator_value_error_propagates_zero_store_calls():
    store = _FakeStore()
    fetch = _make_fetch_history(ValueError("malformed"))

    with pytest.raises(ValueError, match="malformed"):
        _ingest(store, fetch_history=fetch)

    assert len(store.calls) == 0


def test_canonical_mapping_failure_propagates_zero_store_calls():
    bad_raw = BinanceHistoricalFundingRecord(
        symbol="BTCUSDT",
        funding_rate=Decimal("NaN"),
        funding_time=START + timedelta(hours=1),
        mark_price=Decimal("100.50"),
        rate_type="Regular",
    )
    store = _FakeStore()
    fetch = _make_fetch_history([bad_raw])

    with pytest.raises(ValueError, match="finite"):
        _ingest(store, fetch_history=fetch)

    assert len(store.calls) == 0


def test_store_failure_propagates_single_attempt():
    class _Sentinel(Exception):
        pass

    raw = _record(START + timedelta(hours=1))
    store = _FakeStore(raise_exc=_Sentinel("store broke"))
    fetch = _make_fetch_history([raw])

    with pytest.raises(_Sentinel, match="store broke"):
        _ingest(store, fetch_history=fetch)

    assert len(store.calls) == 1


# --- terminal write / forwarding / ordering ---


def test_one_terminal_write_with_complete_event_list():
    raws = [_record(START + timedelta(hours=h)) for h in (1, 2, 3)]
    store = _FakeStore()
    fetch = _make_fetch_history(raws)

    result = _ingest(store, fetch_history=fetch)

    assert len(store.calls) == 1
    assert len(store.calls[0]["events"]) == 3
    assert result == store.calls[0]["events"]


def test_timeout_and_max_attempts_forwarded_unchanged():
    store = _FakeStore()
    fetch = _make_fetch_history([])

    _ingest(store, fetch_history=fetch, timeout=7.5, max_attempts=5)

    assert fetch.calls[0]["timeout"] == 7.5
    assert fetch.calls[0]["max_attempts"] == 5


def test_order_preservation_special_then_regular():
    t = START + timedelta(hours=1)
    special = _record(t, "Special")
    regular = _record(t, "Regular")
    store = _FakeStore()
    fetch = _make_fetch_history([special, regular])

    result = _ingest(store, fetch_history=fetch)

    assert [e.funding.rate_type for e in result] == ["Special", "Regular"]


# --- real SQLite end-to-end ---


def test_real_sqlite_end_to_end(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        t1 = START + timedelta(hours=8)
        t2 = START + timedelta(hours=16)
        normal = _record(t1, "Regular", funding_rate="0.0001", mark_price="65000.5")
        regular_pair = _record(t2, "Regular")
        special_pair = _record(t2, "Special")
        filtered_out = _record(END)

        fetch = _make_fetch_history([normal, regular_pair, special_pair, filtered_out])
        result = _ingest(store, fetch_history=fetch)

        assert len(result) == 3

        stored_events = store.query_events(
            exchange="binance",
            market_type="usdm_perp",
            symbol="BTCUSDT",
            start_time=START,
            end_time=END,
        )
        assert len(stored_events) == 3
        assert stored_events[0].funding.reference_price == Decimal("65000.5")

        coverage = store.query_coverage(
            exchange="binance",
            market_type="usdm_perp",
            symbol="BTCUSDT",
            start_time=START,
            end_time=END,
        )
        assert len(coverage) == 1
        assert coverage[0].start_time == START
        assert coverage[0].end_time == END

        # re-ingestion with identical data: idempotent, no conflict, no duplicate
        fetch2 = _make_fetch_history([normal, regular_pair, special_pair, filtered_out])
        _ingest(store, fetch_history=fetch2)

        stored_events_after = store.query_events(
            exchange="binance",
            market_type="usdm_perp",
            symbol="BTCUSDT",
            start_time=START,
            end_time=END,
        )
        assert len(stored_events_after) == 3

        coverage_after = store.query_coverage(
            exchange="binance",
            market_type="usdm_perp",
            symbol="BTCUSDT",
            start_time=START,
            end_time=END,
        )
        assert len(coverage_after) == 1
    finally:
        store.close()
