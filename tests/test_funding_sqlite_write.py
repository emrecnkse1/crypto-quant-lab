import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.storage.base import DataConflictError, DataCorruptionError

_COVERED_START = datetime(2024, 1, 1, tzinfo=UTC)
_COVERED_END = datetime(2024, 1, 2, tzinfo=UTC)


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


def default_kwargs(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "covered_start": _COVERED_START,
        "covered_end": _COVERED_END,
    }
    fields.update(overrides)
    return fields


def raw_event_rows(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(
            "SELECT exchange, market_type, symbol, event_time_us, rate_type, funding_rate, "
            "reference_price FROM historical_funding_events ORDER BY event_time_us, rate_type"
        ).fetchall()
    finally:
        connection.close()


def raw_coverage_rows(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(
            "SELECT exchange, market_type, symbol, start_time_us, end_time_us "
            "FROM historical_funding_coverage ORDER BY start_time_us, end_time_us"
        ).fetchall()
    finally:
        connection.close()


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


# --- empty events: first-class success path ---


def test_empty_events_still_persists_coverage(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **default_kwargs())
        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == [
            ("binance", "perpetual", "BTCUSDT", 1704067200000000, 1704153600000000)
        ]
    finally:
        store.close()


def test_repeated_empty_events_call_is_idempotent(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        store.write_ingestion_batch((), **default_kwargs())
        store.write_ingestion_batch((), **default_kwargs())
        assert len(raw_coverage_rows(db_path)) == 1
    finally:
        store.close()


# --- event write ---


def test_single_event_is_written_with_coverage(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event()
        store.write_ingestion_batch((event,), **default_kwargs())

        rows = raw_event_rows(db_path)
        assert rows == [
            ("binance", "perpetual", "BTCUSDT", 1704096000000000, "Regular", "0.0001", "50000")
        ]
        assert len(raw_coverage_rows(db_path)) == 1
    finally:
        store.close()


def test_decimal_precision_preserved_exactly(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(
            funding=make_funding_event(
                funding_rate=Decimal("0.000123456789"),
                reference_price=Decimal("102345.67890123"),
            )
        )
        store.write_ingestion_batch((event,), **default_kwargs())

        rows = raw_event_rows(db_path)
        assert rows[0][5] == "0.000123456789"
        assert rows[0][6] == "102345.67890123"
    finally:
        store.close()


# --- same-timestamp multi-type ---


def test_same_timestamp_different_rate_type_both_persist(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        same_time = datetime(2024, 1, 1, 8, tzinfo=UTC)
        regular = make_historical_funding_event(
            funding=make_funding_event(event_time=same_time, rate_type="Regular")
        )
        special = make_historical_funding_event(
            funding=make_funding_event(event_time=same_time, rate_type="Special")
        )
        store.write_ingestion_batch((regular, special), **default_kwargs())

        rows = raw_event_rows(db_path)
        assert len(rows) == 2
        assert {row[4] for row in rows} == {"Regular", "Special"}
        assert len(raw_coverage_rows(db_path)) == 1
    finally:
        store.close()


# --- numeric idempotency ---


def test_numerically_equal_decimal_scale_difference_is_not_a_conflict(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        first = make_historical_funding_event(
            funding=make_funding_event(funding_rate=Decimal("0.0001"))
        )
        second = make_historical_funding_event(
            funding=make_funding_event(funding_rate=Decimal("0.00010"))
        )
        store.write_ingestion_batch((first,), **default_kwargs())
        store.write_ingestion_batch((second,), **default_kwargs())

        rows = raw_event_rows(db_path)
        assert len(rows) == 1
        assert rows[0][5] == "0.0001"  # first stored TEXT representation is preserved
    finally:
        store.close()


# --- in-batch duplicate / conflict ---


def test_in_batch_identical_duplicate_is_idempotent(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event()
        store.write_ingestion_batch((event, event), **default_kwargs())

        assert len(raw_event_rows(db_path)) == 1
        assert len(raw_coverage_rows(db_path)) == 1
    finally:
        store.close()


def test_in_batch_conflict_rolls_back_entire_batch(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event_a = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 4, tzinfo=UTC))
        )
        event_time_k = datetime(2024, 1, 1, 8, tzinfo=UTC)
        event_k_x = make_historical_funding_event(
            funding=make_funding_event(event_time=event_time_k, funding_rate=Decimal("0.0001"))
        )
        event_k_y = make_historical_funding_event(
            funding=make_funding_event(event_time=event_time_k, funding_rate=Decimal("0.0002"))
        )

        with pytest.raises(DataConflictError):
            store.write_ingestion_batch((event_a, event_k_x, event_k_y), **default_kwargs())

        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()


def test_existing_conflict_rolls_back_new_events_and_coverage(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event_time_k = datetime(2024, 1, 1, 20, tzinfo=UTC)
        event_k_x = make_historical_funding_event(
            funding=make_funding_event(event_time=event_time_k, funding_rate=Decimal("0.0001"))
        )
        store.write_ingestion_batch((event_k_x,), **default_kwargs())

        second_covered_start = datetime(2024, 1, 1, 12, tzinfo=UTC)
        second_covered_end = datetime(2024, 1, 2, 12, tzinfo=UTC)
        event_a = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2024, 1, 1, 14, tzinfo=UTC))
        )
        event_k_y = make_historical_funding_event(
            funding=make_funding_event(event_time=event_time_k, funding_rate=Decimal("0.0002"))
        )

        with pytest.raises(DataConflictError):
            store.write_ingestion_batch(
                (event_a, event_k_y),
                **default_kwargs(
                    covered_start=second_covered_start, covered_end=second_covered_end
                ),
            )

        rows = raw_event_rows(db_path)
        assert len(rows) == 1
        assert rows[0][5] == "0.0001"  # original K/X unchanged

        coverage_rows = raw_coverage_rows(db_path)
        assert len(coverage_rows) == 1
        assert coverage_rows[0][3] == 1704067200000000  # original covered_start only
    finally:
        store.close()


# --- partition / range validation ---


def test_partition_mismatch_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(symbol="ETHUSDT")
        with pytest.raises(ValueError, match="partition"):
            store.write_ingestion_batch((event,), **default_kwargs())

        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()


def test_event_at_covered_start_is_legal(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(funding=make_funding_event(event_time=_COVERED_START))
        store.write_ingestion_batch((event,), **default_kwargs())
        assert len(raw_event_rows(db_path)) == 1
    finally:
        store.close()


def test_event_at_covered_end_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(funding=make_funding_event(event_time=_COVERED_END))
        with pytest.raises(ValueError, match="event_time"):
            store.write_ingestion_batch((event,), **default_kwargs())

        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()


def test_event_before_covered_start_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event(
            funding=make_funding_event(event_time=datetime(2023, 12, 31, tzinfo=UTC))
        )
        with pytest.raises(ValueError, match="event_time"):
            store.write_ingestion_batch((event,), **default_kwargs())

        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()


def test_covered_start_equal_end_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        same = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="covered_start"):
            store.write_ingestion_batch((), **default_kwargs(covered_start=same, covered_end=same))
    finally:
        store.close()


def test_covered_start_after_end_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(ValueError, match="covered_start"):
            store.write_ingestion_batch(
                (),
                **default_kwargs(
                    covered_start=datetime(2024, 1, 2, tzinfo=UTC),
                    covered_end=datetime(2024, 1, 1, tzinfo=UTC),
                ),
            )
    finally:
        store.close()


def test_naive_covered_start_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(ValueError):
            store.write_ingestion_batch(
                (),
                **default_kwargs(covered_start=datetime(2024, 1, 1)),  # noqa: DTZ001
            )
    finally:
        store.close()


def test_empty_exchange_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(ValueError, match="exchange"):
            store.write_ingestion_batch((), **default_kwargs(exchange=""))
    finally:
        store.close()


def test_non_str_symbol_is_rejected(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        with pytest.raises(TypeError, match="symbol"):
            store.write_ingestion_batch((), **default_kwargs(symbol=123))
    finally:
        store.close()


def test_invalid_item_in_events_raises_type_error_and_nothing_is_persisted(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        valid_event = make_historical_funding_event()
        with pytest.raises(TypeError):
            store.write_ingestion_batch((valid_event, object()), **default_kwargs())

        assert raw_event_rows(db_path) == []
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()


# --- corrupted existing payload encountered on write ---


def test_corrupted_stored_payload_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    insert_raw_event_row(db_path, funding_rate="Infinity")

    store = SQLiteHistoricalFundingStore(db_path)
    try:
        event = make_historical_funding_event()
        with pytest.raises(DataCorruptionError):
            store.write_ingestion_batch((event,), **default_kwargs())

        rows = raw_event_rows(db_path)
        assert len(rows) == 1
        assert rows[0][5] == "Infinity"
        assert raw_coverage_rows(db_path) == []
    finally:
        store.close()
