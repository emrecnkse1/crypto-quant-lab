import sqlite3
from datetime import UTC, datetime

import pytest

from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

EXPECTED_COLUMNS = [
    # (name, type, notnull, pk_position)
    ("exchange", "TEXT", 1, 1),
    ("market_type", "TEXT", 1, 2),
    ("symbol", "TEXT", 1, 3),
    ("timeframe", "TEXT", 1, 4),
    ("open_time_us", "INTEGER", 1, 5),
    ("open", "TEXT", 1, 0),
    ("high", "TEXT", 1, 0),
    ("low", "TEXT", 1, 0),
    ("close", "TEXT", 1, 0),
    ("volume", "TEXT", 1, 0),
]


def _table_info(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("PRAGMA table_info(historical_candles)").fetchall()
    finally:
        connection.close()
    # (cid, name, type, notnull, dflt_value, pk)
    return [(row[1], row[2], row[3], row[5]) for row in rows]


def test_store_can_be_created_with_temp_sqlite_file(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()
    assert db_path.exists()


def test_historical_candles_table_is_created(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='historical_candles'"
        ).fetchone()
    finally:
        connection.close()

    assert row is not None


def test_columns_have_correct_names_and_order(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    columns = _table_info(db_path)
    column_names_in_order = [name for name, _type, _notnull, _pk in columns]

    assert column_names_in_order == [name for name, *_ in EXPECTED_COLUMNS]


def test_column_types_are_correct(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    columns = {name: col_type for name, col_type, _notnull, _pk in _table_info(db_path)}

    for name, expected_type, _notnull, _pk in EXPECTED_COLUMNS:
        assert columns[name] == expected_type


def test_ohlcv_columns_are_never_real(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    columns = {name: col_type for name, col_type, _notnull, _pk in _table_info(db_path)}

    for name in ("open", "high", "low", "close", "volume"):
        assert columns[name] != "REAL"
        assert columns[name] == "TEXT"


def test_all_columns_are_not_null(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    for _name, _type, notnull, _pk in _table_info(db_path):
        assert notnull == 1


def test_composite_primary_key_covers_canonical_key_in_order(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    pk_columns = sorted(
        ((pk, name) for name, _type, _notnull, pk in _table_info(db_path) if pk > 0),
    )
    ordered_pk_names = [name for _pk, name in pk_columns]

    assert ordered_pk_names == ["exchange", "market_type", "symbol", "timeframe", "open_time_us"]


def test_reopening_existing_database_does_not_raise(tmp_path):
    db_path = tmp_path / "historical.db"

    first_store = SQLiteHistoricalCandleStore(db_path)
    first_store.close()

    second_store = SQLiteHistoricalCandleStore(db_path)
    second_store.close()

    assert db_path.exists()


def test_close_closes_the_connection(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)

    store.close()

    with pytest.raises(sqlite3.ProgrammingError):
        store._connection.execute("SELECT 1")


def test_write_batch_raises_not_implemented(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(NotImplementedError):
            store.write_batch([])
    finally:
        store.close()


def test_query_raises_not_implemented(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(NotImplementedError):
            store.query(
                "binance",
                "spot",
                "BTCUSDT",
                "1h",
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 2, tzinfo=UTC),
            )
    finally:
        store.close()
