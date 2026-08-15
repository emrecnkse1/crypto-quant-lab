import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.storage.base import DataCorruptionError, StorageError

_VALID_EVENTS_SQL = """
CREATE TABLE historical_funding_events (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_time_us INTEGER NOT NULL,
    rate_type TEXT NOT NULL,
    funding_rate TEXT NOT NULL,
    reference_price TEXT NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
)
"""

_VALID_COVERAGE_SQL = """
CREATE TABLE historical_funding_coverage (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_time_us INTEGER NOT NULL,
    end_time_us INTEGER NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, start_time_us, end_time_us)
)
"""


def create_raw_events_table(db_path, create_table_sql):
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(create_table_sql)
            connection.execute(_VALID_COVERAGE_SQL)
    finally:
        connection.close()


def create_raw_coverage_table(db_path, create_table_sql):
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(_VALID_EVENTS_SQL)
            connection.execute(create_table_sql)
    finally:
        connection.close()


def raw_table_info(db_path, table_name):
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        connection.close()
    return [(row[1], row[2], row[3], row[5]) for row in sorted(rows, key=lambda row: row[0])]


# --- fresh / reopen success paths ---


def test_brand_new_database_opens_successfully(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    try:
        assert db_path.exists()
    finally:
        store.close()


def test_canonical_existing_database_reopens_successfully(tmp_path):
    db_path = tmp_path / "funding.db"
    first_store = SQLiteHistoricalFundingStore(db_path)
    first_store.close()

    second_store = SQLiteHistoricalFundingStore(db_path)
    try:
        assert db_path.exists()
    finally:
        second_store.close()


def test_canonical_existing_database_with_data_preserves_data_on_reopen(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    event = HistoricalFundingEvent(
        exchange="binance",
        market_type="perpetual",
        symbol="BTCUSDT",
        funding=FundingEvent(
            event_time=datetime(2024, 1, 1, 8, tzinfo=UTC),
            funding_rate=Decimal("0.0001"),
            reference_price=Decimal(50000),
            rate_type="Regular",
        ),
    )
    store.write_ingestion_batch(
        (event,),
        exchange="binance",
        market_type="perpetual",
        symbol="BTCUSDT",
        covered_start=datetime(2024, 1, 1, tzinfo=UTC),
        covered_end=datetime(2024, 1, 2, tzinfo=UTC),
    )
    store.close()

    reopened = SQLiteHistoricalFundingStore(db_path)
    try:
        events = reopened.query_events(
            exchange="binance",
            market_type="perpetual",
            symbol="BTCUSDT",
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            end_time=datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert events == [event]
        coverage = reopened.query_coverage(
            exchange="binance",
            market_type="perpetual",
            symbol="BTCUSDT",
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            end_time=datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(coverage) == 1
    finally:
        reopened.close()


# --- malformed event table ---


def test_event_table_missing_column_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_events_table(
        db_path,
        """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_event_table_extra_column_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_events_table(
        db_path,
        """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate TEXT NOT NULL,
            reference_price TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_event_table_wrong_type_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_events_table(
        db_path,
        """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate REAL NOT NULL,
            reference_price TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_event_table_missing_not_null_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_events_table(
        db_path,
        """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate TEXT,
            reference_price TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_event_table_wrong_primary_key_order_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_events_table(
        db_path,
        """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate TEXT NOT NULL,
            reference_price TEXT NOT NULL,
            PRIMARY KEY (market_type, exchange, symbol, event_time_us, rate_type)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


# --- malformed coverage table ---


def test_coverage_table_missing_column_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_coverage_table(
        db_path,
        """
        CREATE TABLE historical_funding_coverage (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            start_time_us INTEGER NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, start_time_us)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_coverage_table_wrong_type_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_coverage_table(
        db_path,
        """
        CREATE TABLE historical_funding_coverage (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            start_time_us TEXT NOT NULL,
            end_time_us INTEGER NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, start_time_us, end_time_us)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


def test_coverage_table_missing_primary_key_member_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "funding.db"
    create_raw_coverage_table(
        db_path,
        """
        CREATE TABLE historical_funding_coverage (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            start_time_us INTEGER NOT NULL,
            end_time_us INTEGER NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, start_time_us)
        )
        """,
    )
    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)


# --- failure discipline: no repair, connection cleanup ---


def test_failed_event_validation_does_not_alter_incompatible_schema(tmp_path):
    db_path = tmp_path / "funding.db"
    incompatible_sql = """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate REAL NOT NULL,
            reference_price TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """
    create_raw_events_table(db_path, incompatible_sql)

    schema_before = raw_table_info(db_path, "historical_funding_events")

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalFundingStore(db_path)

    schema_after = raw_table_info(db_path, "historical_funding_events")
    assert schema_after == schema_before
    assert ("funding_rate", "REAL", 1, 0) in schema_after


def test_failed_validation_closes_the_connection(tmp_path):
    db_path = tmp_path / "funding.db"
    incompatible_sql = """
        CREATE TABLE historical_funding_events (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_time_us INTEGER NOT NULL,
            rate_type TEXT NOT NULL,
            funding_rate REAL NOT NULL,
            reference_price TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
        )
        """
    create_raw_events_table(db_path, incompatible_sql)

    try:
        SQLiteHistoricalFundingStore(db_path)
        pytest.fail("expected DataCorruptionError")
    except DataCorruptionError as exc:
        assert "schema mismatch" in str(exc)

    # the database file is not left locked by a leaked connection
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("SELECT 1")
    finally:
        connection.close()


def test_corrupt_file_initialization_raises_storage_error_and_cleans_up(tmp_path):
    db_path = tmp_path / "funding.db"
    corrupt_bytes = b"not a valid sqlite database file, just garbage bytes"
    db_path.write_bytes(corrupt_bytes)

    with pytest.raises(StorageError) as exc_info:
        SQLiteHistoricalFundingStore(db_path)

    assert isinstance(exc_info.value.__cause__, sqlite3.Error)
    assert db_path.read_bytes() == corrupt_bytes

    # no connection leaked: a clean delete proves the store closed its connection
    db_path.unlink()
