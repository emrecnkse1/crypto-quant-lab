import sqlite3
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.storage.base import DataCorruptionError

_COVERED_START = datetime(2024, 1, 1, tzinfo=UTC)
_COVERED_END = datetime(2024, 1, 2, tzinfo=UTC)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def make_funding_event(**overrides):
    fields = {
        "event_time": datetime(2024, 1, 1, 8, tzinfo=UTC),
        "funding_rate": Decimal("0.0001"),
        "reference_price": Decimal(50000),
        "rate_type": "Regular",
    }
    fields.update(overrides)
    return FundingEvent(**fields)


def make_historical_funding_event(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "funding": make_funding_event(),
    }
    fields.update(overrides)
    return HistoricalFundingEvent(**fields)


def write_kwargs(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "covered_start": _COVERED_START,
        "covered_end": _COVERED_END,
    }
    fields.update(overrides)
    return fields


def query_kwargs(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "start_time": _COVERED_START,
        "end_time": _COVERED_END,
    }
    fields.update(overrides)
    return fields


def insert_raw_event_row(db_path, **column_overrides):
    values = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "event_time_us": 1704096000000000,
        "rate_type": "Regular",
        "funding_rate": "0.0001",
        "reference_price": "50000",
    }
    values.update(column_overrides)
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(
                "INSERT INTO historical_funding_events ("
                "exchange, market_type, symbol, event_time_us, rate_type, funding_rate, "
                "reference_price) VALUES (:exchange, :market_type, :symbol, :event_time_us, "
                ":rate_type, :funding_rate, :reference_price)",
                values,
            )
    finally:
        connection.close()


def insert_raw_coverage_row(db_path, **column_overrides):
    values = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "start_time_us": 1704067200000000,
        "end_time_us": 1704153600000000,
    }
    values.update(column_overrides)
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(
                "INSERT INTO historical_funding_coverage ("
                "exchange, market_type, symbol, start_time_us, end_time_us) "
                "VALUES (:exchange, :market_type, :symbol, :start_time_us, :end_time_us)",
                values,
            )
    finally:
        connection.close()


def row_counts(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        events = connection.execute("SELECT COUNT(*) FROM historical_funding_events").fetchone()[0]
        coverage = connection.execute(
            "SELECT COUNT(*) FROM historical_funding_coverage"
        ).fetchone()[0]
    finally:
        connection.close()
    return events, coverage


# =====================================================================
# query_events
# =====================================================================


def test_query_events_on_empty_database_returns_empty_list(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        assert store.query_events(**query_kwargs()) == []
    finally:
        store.close()


def test_query_events_single_matching_event_is_returned(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event()
        store.write_ingestion_batch((event,), **write_kwargs())

        result = store.query_events(**query_kwargs())
        assert result == [event]
    finally:
        store.close()


def test_query_events_start_boundary_is_inclusive(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(funding=make_funding_event(event_time=_COVERED_START))
        store.write_ingestion_batch((event,), **write_kwargs())

        result = store.query_events(**query_kwargs())
        assert result == [event]
    finally:
        store.close()


def test_query_events_end_boundary_is_exclusive(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        result = store.query_events(
            **query_kwargs(start_time=_COVERED_START, end_time=datetime(2024, 1, 1, 8, tzinfo=UTC))
        )
        assert result == []
    finally:
        store.close()


def test_query_events_are_returned_in_chronological_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        late = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 16, tzinfo=UTC))
        )
        early = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 4, tzinfo=UTC))
        )
        mid = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 8, tzinfo=UTC))
        )
        # written out of chronological order across separate calls (adjacent covered ranges)
        store.write_ingestion_batch(
            (early, mid, late),
            **write_kwargs(covered_start=_COVERED_START, covered_end=_COVERED_END),
        )

        result = store.query_events(**query_kwargs())
        assert [r.funding.event_time for r in result] == [
            early.funding.event_time,
            mid.funding.event_time,
            late.funding.event_time,
        ]
    finally:
        store.close()


def test_query_events_same_timestamp_multi_type_deterministic_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        same_time = datetime(2024, 1, 1, 8, tzinfo=UTC)
        special = make_historical_funding_event(
            funding=make_funding_event(event_time=same_time, rate_type="Special")
        )
        regular = make_historical_funding_event(
            funding=make_funding_event(event_time=same_time, rate_type="Regular")
        )
        # written in reverse-lexical order to prove SQL, not insertion order, decides
        store.write_ingestion_batch((special, regular), **write_kwargs())

        result = store.query_events(**query_kwargs())
        assert [r.funding.rate_type for r in result] == ["Regular", "Special"]
    finally:
        store.close()


def test_query_events_filters_by_exchange(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch(
            (make_historical_funding_event(exchange="binance"),),
            **write_kwargs(exchange="binance"),
        )
        store.write_ingestion_batch(
            (make_historical_funding_event(exchange="other_exchange"),),
            **write_kwargs(exchange="other_exchange"),
        )

        result = store.query_events(**query_kwargs(exchange="binance"))
        assert len(result) == 1
        assert result[0].exchange == "binance"
    finally:
        store.close()


def test_query_events_filters_by_market_type(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch(
            (make_historical_funding_event(market_type="perpetual"),),
            **write_kwargs(market_type="perpetual"),
        )
        store.write_ingestion_batch(
            (make_historical_funding_event(market_type="futures"),),
            **write_kwargs(market_type="futures"),
        )

        result = store.query_events(**query_kwargs(market_type="perpetual"))
        assert len(result) == 1
        assert result[0].market_type == "perpetual"
    finally:
        store.close()


def test_query_events_filters_by_symbol(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch(
            (make_historical_funding_event(symbol="BTCUSDT"),), **write_kwargs(symbol="BTCUSDT")
        )
        store.write_ingestion_batch(
            (make_historical_funding_event(symbol="ETHUSDT"),), **write_kwargs(symbol="ETHUSDT")
        )

        result = store.query_events(**query_kwargs(symbol="BTCUSDT"))
        assert len(result) == 1
        assert result[0].symbol == "BTCUSDT"
    finally:
        store.close()


def test_query_events_non_utc_boundaries_normalize_to_same_instant(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event_time = datetime(2024, 1, 1, 8, tzinfo=UTC)
        event = make_historical_funding_event(funding=make_funding_event(event_time=event_time))
        store.write_ingestion_batch((event,), **write_kwargs())

        plus_five = timezone(timedelta(hours=5))
        start_local = _COVERED_START.astimezone(plus_five)
        end_local = _COVERED_END.astimezone(plus_five)

        result = store.query_events(**query_kwargs(start_time=start_local, end_time=end_local))
        assert result == [event]
    finally:
        store.close()


def test_query_events_naive_start_time_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.query_events(**query_kwargs(start_time=datetime(2024, 1, 1)))  # noqa: DTZ001
    finally:
        store.close()


def test_query_events_pseudo_naive_end_time_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.query_events(
                **query_kwargs(end_time=datetime(2024, 1, 2, tzinfo=_BrokenTzInfo()))
            )
    finally:
        store.close()


def test_query_events_equal_start_and_end_time_raises_value_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        same = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError):
            store.query_events(**query_kwargs(start_time=same, end_time=same))
    finally:
        store.close()


def test_query_events_decimal_precision_round_trips_exactly(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(
            funding=make_funding_event(
                funding_rate=Decimal("0.000123456789"),
                reference_price=Decimal("102345.67890123"),
            )
        )
        store.write_ingestion_batch((event,), **write_kwargs())

        result = store.query_events(**query_kwargs())
        assert result[0].funding.funding_rate == Decimal("0.000123456789")
        assert result[0].funding.reference_price == Decimal("102345.67890123")
    finally:
        store.close()


# --- query_events corruption ---


def test_query_events_malformed_decimal_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_event_row(db_path, funding_rate="not-a-decimal")

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_events(**query_kwargs())
    finally:
        store.close()


def test_query_events_non_positive_reference_price_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_event_row(db_path, reference_price="0")

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_events(**query_kwargs())
    finally:
        store.close()


def test_query_events_empty_rate_type_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_event_row(db_path, rate_type="")

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_events(**query_kwargs())
    finally:
        store.close()


def test_query_events_type_corrupted_event_time_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_event_row(db_path, event_time_us=1704096000000000.5)

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_events(**query_kwargs())
    finally:
        store.close()


# =====================================================================
# query_coverage
# =====================================================================


def test_query_coverage_no_overlap_returns_empty_list(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs())

        result = store.query_coverage(
            **query_kwargs(
                start_time=datetime(2025, 1, 1, tzinfo=UTC),
                end_time=datetime(2025, 1, 2, tzinfo=UTC),
            )
        )
        assert result == []
    finally:
        store.close()


def test_query_coverage_containing_stored_interval_is_returned_unclipped(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs())  # stores [00:00, 24:00) on day 1

        result = store.query_coverage(
            **query_kwargs(
                start_time=datetime(2024, 1, 1, 6, tzinfo=UTC),
                end_time=datetime(2024, 1, 1, 12, tzinfo=UTC),
            )
        )
        assert len(result) == 1
        assert result[0].start_time == _COVERED_START
        assert result[0].end_time == _COVERED_END
    finally:
        store.close()


def test_query_coverage_partial_overlap_is_returned(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs())  # stores [day1 00:00, day2 00:00)

        result = store.query_coverage(
            **query_kwargs(
                start_time=datetime(2024, 1, 1, 18, tzinfo=UTC),
                end_time=datetime(2024, 1, 2, 6, tzinfo=UTC),
            )
        )
        assert len(result) == 1
        assert result[0].start_time == _COVERED_START
        assert result[0].end_time == _COVERED_END
    finally:
        store.close()


def test_query_coverage_touching_end_boundary_is_not_overlap(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs())  # stores [day1 00:00, day2 00:00)

        # requested range starts exactly where stored coverage ends
        result = store.query_coverage(
            **query_kwargs(start_time=_COVERED_END, end_time=datetime(2024, 1, 3, tzinfo=UTC))
        )
        assert result == []
    finally:
        store.close()


def test_query_coverage_touching_start_boundary_is_not_overlap(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs())  # stores [day1 00:00, day2 00:00)

        # requested range ends exactly where stored coverage starts
        result = store.query_coverage(
            **query_kwargs(start_time=datetime(2023, 12, 31, tzinfo=UTC), end_time=_COVERED_START)
        )
        assert result == []
    finally:
        store.close()


def test_query_coverage_adjacent_intervals_remain_separate(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch(
            (),
            **write_kwargs(
                covered_start=datetime(2024, 1, 1, tzinfo=UTC),
                covered_end=datetime(2024, 1, 1, 12, tzinfo=UTC),
            ),
        )
        store.write_ingestion_batch(
            (),
            **write_kwargs(
                covered_start=datetime(2024, 1, 1, 12, tzinfo=UTC),
                covered_end=datetime(2024, 1, 2, tzinfo=UTC),
            ),
        )

        result = store.query_coverage(**query_kwargs())
        assert len(result) == 2
        assert result[0].start_time == datetime(2024, 1, 1, tzinfo=UTC)
        assert result[0].end_time == datetime(2024, 1, 1, 12, tzinfo=UTC)
        assert result[1].start_time == datetime(2024, 1, 1, 12, tzinfo=UTC)
        assert result[1].end_time == datetime(2024, 1, 2, tzinfo=UTC)
    finally:
        store.close()


def test_query_coverage_filters_by_metadata_partition(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **write_kwargs(symbol="BTCUSDT"))
        store.write_ingestion_batch((), **write_kwargs(symbol="ETHUSDT"))

        result = store.query_coverage(**query_kwargs(symbol="BTCUSDT"))
        assert len(result) == 1
    finally:
        store.close()


def test_query_coverage_malformed_range_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    # stored start/end swapped (start > end); query range widened so the SQL
    # overlap predicate still matches this malformed row and reconstruction
    # is actually attempted
    insert_raw_coverage_row(db_path, start_time_us=1704153600000000, end_time_us=1704067200000000)

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_coverage(
                **query_kwargs(
                    start_time=datetime(2023, 12, 31, 23, tzinfo=UTC),
                    end_time=datetime(2024, 1, 2, 1, tzinfo=UTC),
                )
            )
    finally:
        store.close()


def test_query_coverage_type_corrupted_timestamp_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_coverage_row(db_path, end_time_us=1704153600000000.5)

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.query_coverage(**query_kwargs())
    finally:
        store.close()


# =====================================================================
# read-only / store remains usable
# =====================================================================


def test_queries_are_read_only_and_store_remains_usable_afterward(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event()
        store.write_ingestion_batch((event,), **write_kwargs())
        counts_before = row_counts(db_path)

        store.query_events(**query_kwargs())
        store.query_coverage(**query_kwargs())

        assert row_counts(db_path) == counts_before

        # store remains usable for further writes/queries
        second = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 16, tzinfo=UTC))
        )
        store.write_ingestion_batch((second,), **write_kwargs())
        result = store.query_events(**query_kwargs())
        assert len(result) == 2
    finally:
        store.close()
