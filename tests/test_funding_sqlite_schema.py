import sqlite3

import pytest

from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore

EXPECTED_EVENT_COLUMNS = [
    # (name, type, notnull, pk_position)
    ("exchange", "TEXT", 1, 1),
    ("market_type", "TEXT", 1, 2),
    ("symbol", "TEXT", 1, 3),
    ("event_time_us", "INTEGER", 1, 4),
    ("rate_type", "TEXT", 1, 5),
    ("funding_rate", "TEXT", 1, 0),
    ("reference_price", "TEXT", 1, 0),
]

EXPECTED_COVERAGE_COLUMNS = [
    ("exchange", "TEXT", 1, 1),
    ("market_type", "TEXT", 1, 2),
    ("symbol", "TEXT", 1, 3),
    ("start_time_us", "INTEGER", 1, 4),
    ("end_time_us", "INTEGER", 1, 5),
]


def _table_info(db_path, table_name):
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        connection.close()
    # (cid, name, type, notnull, dflt_value, pk)
    return [(row[1], row[2], row[3], row[5]) for row in rows]


def _table_exists(db_path, table_name):
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
    finally:
        connection.close()
    return row is not None


# --- fresh schema creation ---


def test_store_can_be_created_with_temp_sqlite_file(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()
    assert db_path.exists()


def test_historical_funding_events_table_is_created(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    assert _table_exists(db_path, "historical_funding_events")


def test_historical_funding_coverage_table_is_created(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    assert _table_exists(db_path, "historical_funding_coverage")


# --- event table shape ---


def test_event_table_columns_have_correct_names_and_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    columns = _table_info(db_path, "historical_funding_events")
    column_names_in_order = [name for name, _type, _notnull, _pk in columns]

    assert column_names_in_order == [name for name, *_ in EXPECTED_EVENT_COLUMNS]


def test_event_table_column_types_are_correct(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    columns = {
        name: col_type
        for name, col_type, _notnull, _pk in _table_info(db_path, "historical_funding_events")
    }

    for name, expected_type, _notnull, _pk in EXPECTED_EVENT_COLUMNS:
        assert columns[name] == expected_type


def test_event_table_payload_columns_are_never_real(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    columns = {
        name: col_type
        for name, col_type, _notnull, _pk in _table_info(db_path, "historical_funding_events")
    }

    for name in ("funding_rate", "reference_price"):
        assert columns[name] != "REAL"
        assert columns[name] == "TEXT"


def test_event_table_all_columns_are_not_null(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    for _name, _type, notnull, _pk in _table_info(db_path, "historical_funding_events"):
        assert notnull == 1


def test_event_table_primary_key_covers_canonical_key_in_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    pk_columns = sorted(
        (pk, name)
        for name, _type, _notnull, pk in _table_info(db_path, "historical_funding_events")
        if pk > 0
    )
    ordered_pk_names = [name for _pk, name in pk_columns]

    assert ordered_pk_names == ["exchange", "market_type", "symbol", "event_time_us", "rate_type"]


# --- coverage table shape ---


def test_coverage_table_columns_have_correct_names_and_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    columns = _table_info(db_path, "historical_funding_coverage")
    column_names_in_order = [name for name, _type, _notnull, _pk in columns]

    assert column_names_in_order == [name for name, *_ in EXPECTED_COVERAGE_COLUMNS]


def test_coverage_table_column_types_are_correct(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    columns = {
        name: col_type
        for name, col_type, _notnull, _pk in _table_info(db_path, "historical_funding_coverage")
    }

    for name, expected_type, _notnull, _pk in EXPECTED_COVERAGE_COLUMNS:
        assert columns[name] == expected_type


def test_coverage_table_all_columns_are_not_null(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    for _name, _type, notnull, _pk in _table_info(db_path, "historical_funding_coverage"):
        assert notnull == 1


def test_coverage_table_primary_key_covers_identity_in_order(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    pk_columns = sorted(
        (pk, name)
        for name, _type, _notnull, pk in _table_info(db_path, "historical_funding_coverage")
        if pk > 0
    )
    ordered_pk_names = [name for _pk, name in pk_columns]

    assert ordered_pk_names == ["exchange", "market_type", "symbol", "start_time_us", "end_time_us"]


# --- reopening / existing table preservation ---


def test_reopening_existing_database_does_not_raise(tmp_path):
    db_path = tmp_path / "funding.db"

    first_store = SQLiteHistoricalFundingStore(db_path)
    first_store.close()

    second_store = SQLiteHistoricalFundingStore(db_path)
    second_store.close()

    assert db_path.exists()


def test_unrelated_existing_table_is_left_untouched(tmp_path):
    db_path = tmp_path / "funding.db"

    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute("CREATE TABLE historical_candles (marker TEXT)")
            connection.execute("INSERT INTO historical_candles VALUES ('keep-me')")
    finally:
        connection.close()

    store = SQLiteHistoricalFundingStore(db_path)
    store.close()

    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT marker FROM historical_candles").fetchone()
    finally:
        connection.close()

    assert row == ("keep-me",)
    assert _table_exists(db_path, "historical_funding_events")
    assert _table_exists(db_path, "historical_funding_coverage")


# --- close ---


def test_close_closes_the_connection(tmp_path):
    db_path = tmp_path / "funding.db"
    store = SQLiteHistoricalFundingStore(db_path)

    store.close()

    with pytest.raises(sqlite3.ProgrammingError):
        store._connection.execute("SELECT 1")
