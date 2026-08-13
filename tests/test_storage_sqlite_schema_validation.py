import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import DataCorruptionError, HistoricalCandle, StorageError
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

_CANONICAL_CREATE_TABLE_SQL = """
CREATE TABLE historical_candles (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time_us INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
)
"""


def create_raw_table(db_path, create_table_sql, sentinel_row=None):
    """Create a table with arbitrary raw SQL, bypassing the store entirely."""
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(create_table_sql)
            if sentinel_row is not None:
                columns = ", ".join(sentinel_row.keys())
                placeholders = ", ".join(f":{name}" for name in sentinel_row)
                connection.execute(
                    f"INSERT INTO historical_candles ({columns}) VALUES ({placeholders})",
                    sentinel_row,
                )
    finally:
        connection.close()


def raw_table_info(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("PRAGMA table_info(historical_candles)").fetchall()
    finally:
        connection.close()
    # (cid, name, type, notnull, dflt_value, pk) -> (name, type, notnull, pk), cid order
    return [(row[1], row[2], row[3], row[5]) for row in sorted(rows, key=lambda row: row[0])]


def raw_rows(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("SELECT * FROM historical_candles").fetchall()
    finally:
        connection.close()


def make_candle(**overrides):
    fields = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "open_time": datetime(2024, 1, 1, tzinfo=UTC),
        "open": Decimal(100),
        "high": Decimal(110),
        "low": Decimal(90),
        "close": Decimal(105),
        "volume": Decimal(10),
    }
    fields.update(overrides)
    return Candle(**fields)


def make_record(exchange="binance", market_type="spot", **candle_overrides):
    return HistoricalCandle(
        exchange=exchange, market_type=market_type, candle=make_candle(**candle_overrides)
    )


# A) brand-new DB -> store opens successfully


def test_brand_new_database_opens_successfully(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        assert db_path.exists()
    finally:
        store.close()


# B) canonical existing DB -> reopen succeeds


def test_canonical_existing_database_reopens_successfully(tmp_path):
    db_path = tmp_path / "historical.db"
    first_store = SQLiteHistoricalCandleStore(db_path)
    first_store.close()

    second_store = SQLiteHistoricalCandleStore(db_path)
    try:
        assert db_path.exists()
    finally:
        second_store.close()


# C) canonical existing DB + data -> reopen preserves data and remains queryable


def test_canonical_existing_database_with_data_preserves_data_on_reopen(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    record = make_record()
    store.write_batch([record])
    store.close()

    reopened = SQLiteHistoricalCandleStore(db_path)
    try:
        result = reopened.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert result == [record]
    finally:
        reopened.close()


# D) missing column -> DataCorruptionError


def test_missing_column_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# E) extra column -> DataCorruptionError


def test_extra_column_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# F) wrong column name -> DataCorruptionError


def test_wrong_column_name_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, ticker, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# G) OHLCV type TEXT -> REAL -> DataCorruptionError


def test_ohlcv_real_type_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open REAL NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# H) open_time_us INTEGER -> TEXT -> DataCorruptionError


def test_open_time_us_text_type_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us TEXT NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# I) expected NOT NULL missing -> DataCorruptionError


def test_missing_not_null_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# J) wrong primary-key order -> DataCorruptionError


def test_wrong_primary_key_order_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (market_type, exchange, symbol, timeframe, open_time_us)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# K) missing primary-key member -> DataCorruptionError


def test_missing_primary_key_member_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe)
        )
        """,
    )

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)


# L) failed validation does not drop/recreate the incompatible table


def test_failed_validation_does_not_alter_incompatible_schema(tmp_path):
    db_path = tmp_path / "historical.db"
    incompatible_sql = """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open REAL NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """
    create_raw_table(db_path, incompatible_sql)

    schema_before = raw_table_info(db_path)

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)

    schema_after = raw_table_info(db_path)
    assert schema_after == schema_before
    assert ("open", "REAL", 1, 0) in schema_after


# M) sentinel data in an incompatible schema survives a failed validation attempt


def test_failed_validation_preserves_existing_sentinel_data(tmp_path):
    db_path = tmp_path / "historical.db"
    incompatible_sql = """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """
    sentinel_row = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "open_time_us": 1704067200000000,
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "10",
        "note": "sentinel",
    }
    create_raw_table(db_path, incompatible_sql, sentinel_row=sentinel_row)

    rows_before = raw_rows(db_path)
    assert len(rows_before) == 1

    with pytest.raises(DataCorruptionError):
        SQLiteHistoricalCandleStore(db_path)

    rows_after = raw_rows(db_path)
    assert rows_after == rows_before


# Constructor safety: the sqlite connection is closed, not leaked, on validation failure


def test_failed_validation_closes_the_connection(tmp_path):
    db_path = tmp_path / "historical.db"
    create_raw_table(
        db_path,
        """
        CREATE TABLE historical_candles (
            exchange TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time_us INTEGER NOT NULL,
            open REAL NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            PRIMARY KEY (exchange, market_type, symbol, timeframe, open_time_us)
        )
        """,
    )

    try:
        SQLiteHistoricalCandleStore(db_path)
        pytest.fail("expected DataCorruptionError")
    except DataCorruptionError as exc:
        # the underlying instance never escaped __init__; nothing to close from
        # the caller's side. The file itself must still be usable afterward.
        assert "schema mismatch" in str(exc)

    # the database file is not left locked by a leaked connection
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("SELECT 1")
    finally:
        connection.close()


# Constructor safety: cleanup when the CREATE TABLE step itself fails, not just
# when schema validation fails. A file that exists but is not a valid SQLite
# database makes the very first statement executed in __init__ (CREATE TABLE
# IF NOT EXISTS) fail immediately with a genuine sqlite3.Error, before schema
# validation ever runs — this exercises the earlier branch of the same
# try/except cleanup block.
#
# The backend-neutral contract (HISTORICAL_DATA_SPEC.md Bölüm 15) requires
# that no sqlite3-specific exception ever reaches the caller of this
# Protocol-based store: an unexpected sqlite3.Error during CREATE TABLE must
# surface as StorageError, with the original sqlite3 exception preserved as
# __cause__ for diagnostics.


def test_create_table_failure_wraps_as_storage_error_and_cleans_up_connection(tmp_path):
    db_path = tmp_path / "historical.db"
    corrupt_bytes = b"not a valid sqlite database file, just garbage bytes"
    db_path.write_bytes(corrupt_bytes)

    with pytest.raises(StorageError) as exc_info:
        SQLiteHistoricalCandleStore(db_path)

    assert isinstance(exc_info.value.__cause__, sqlite3.Error)

    # the file was not touched by the failed CREATE TABLE attempt
    assert db_path.read_bytes() == corrupt_bytes

    # no connection was leaked: on Windows, a still-open sqlite3 connection
    # holds an OS-level file lock that would make this delete fail with
    # PermissionError. A clean delete is direct evidence the store closed
    # its connection during __init__'s except-block cleanup.
    db_path.unlink()


# Named-object collision: an existing INDEX named "historical_candles" makes
# `CREATE TABLE IF NOT EXISTS historical_candles (...)` itself fail with a
# genuine sqlite3.OperationalError ("there is already an index named ..."),
# exercising the same wrapping/cleanup path with a realistic, naturally
# occurring collision rather than a corrupted file.


def test_index_name_collision_wraps_as_storage_error_and_leaves_objects_untouched(tmp_path):
    db_path = tmp_path / "historical.db"
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute("CREATE TABLE other_table (a INTEGER)")
            connection.execute("CREATE INDEX historical_candles ON other_table(a)")
    finally:
        connection.close()

    with pytest.raises(StorageError) as exc_info:
        SQLiteHistoricalCandleStore(db_path)

    assert isinstance(exc_info.value.__cause__, sqlite3.Error)

    # no automatic repair/drop/migration happened: both pre-existing objects
    # are exactly as they were before the failed open attempt
    connection = sqlite3.connect(str(db_path))
    try:
        objects = connection.execute(
            "SELECT type, name, tbl_name FROM sqlite_master ORDER BY name"
        ).fetchall()
    finally:
        connection.close()

    assert ("table", "other_table", "other_table") in objects
    assert ("index", "historical_candles", "other_table") in objects
    assert not any(obj[1] == "historical_candles" and obj[0] == "table" for obj in objects)
