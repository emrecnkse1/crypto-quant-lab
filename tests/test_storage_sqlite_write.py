import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import DataConflictError, DataCorruptionError, HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

_COLUMNS_SQL = (
    "exchange, market_type, symbol, timeframe, open_time_us, open, high, low, close, volume"
)


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


def raw_rows(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(
            f"SELECT {_COLUMNS_SQL} FROM historical_candles ORDER BY open_time_us"
        ).fetchall()
    finally:
        connection.close()


def insert_raw_row(db_path, **column_overrides):
    values = {
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
    }
    values.update(column_overrides)
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            connection.execute(
                f"INSERT INTO historical_candles ({_COLUMNS_SQL}) "
                "VALUES (:exchange, :market_type, :symbol, :timeframe, :open_time_us, "
                ":open, :high, :low, :close, :volume)",
                values,
            )
    finally:
        connection.close()


# A) empty batch


def test_empty_batch_is_a_no_op(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([])
        assert raw_rows(db_path) == []
    finally:
        store.close()


# B) single record write


def test_single_record_is_written(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record()])
        assert len(raw_rows(db_path)) == 1
    finally:
        store.close()


# C) raw row types and exact Decimal representation


def test_raw_row_has_correct_types_and_exact_decimal_representation(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(open=Decimal("100.12345678"))])
    finally:
        store.close()

    rows = raw_rows(db_path)
    assert len(rows) == 1
    _exchange, _market_type, _symbol, _timeframe, open_time_us, open_, high, low, close, volume = (
        rows[0]
    )
    assert isinstance(open_time_us, int)
    for value in (open_, high, low, close, volume):
        assert isinstance(value, str)
    assert open_ == "100.12345678"


# D) multiple distinct records in the same batch


def test_multiple_distinct_records_are_written_in_same_batch(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record_a = make_record(open_time=datetime(2024, 1, 1, tzinfo=UTC))
        record_b = make_record(open_time=datetime(2024, 1, 1, 1, tzinfo=UTC))
        store.write_batch([record_a, record_b])
    finally:
        store.close()

    assert len(raw_rows(db_path)) == 2


# E) same record twice in the same batch


def test_same_record_twice_in_same_batch_is_idempotent(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record = make_record()
        store.write_batch([record, record])
        assert len(raw_rows(db_path)) == 1
    finally:
        store.close()


# F) same record across separate write_batch calls


def test_same_record_across_separate_write_batch_calls_is_idempotent(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record = make_record()
        store.write_batch([record])
        store.write_batch([record])
        assert len(raw_rows(db_path)) == 1
    finally:
        store.close()


# G) numerically-equal Decimal scale difference is not a conflict


def test_numerically_equal_decimal_scale_difference_is_not_a_conflict(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(volume=Decimal("1.0"))])
        store.write_batch([make_record(volume=Decimal("1.00"))])
    finally:
        store.close()

    rows = raw_rows(db_path)
    assert len(rows) == 1
    assert rows[0][9] == "1.0"  # first stored TEXT representation is preserved


# H) existing canonical key + different OHLCV -> conflict, original unchanged


def test_conflicting_duplicate_raises_and_leaves_original_row_unchanged(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(volume=Decimal(10))])

        with pytest.raises(DataConflictError):
            store.write_batch([make_record(volume=Decimal(20))])

        rows = raw_rows(db_path)
        assert len(rows) == 1
        assert rows[0][9] == "10"
    finally:
        store.close()


# I) late same-batch conflict rolls back the whole batch, including earlier inserts


def test_late_conflict_rolls_back_entire_batch(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record_a = make_record(open_time=datetime(2024, 1, 1, tzinfo=UTC), volume=Decimal(10))
        store.write_batch([record_a])

        record_b_new = make_record(open_time=datetime(2024, 1, 1, 1, tzinfo=UTC))
        record_a_conflict = make_record(
            open_time=datetime(2024, 1, 1, tzinfo=UTC), volume=Decimal(20)
        )

        with pytest.raises(DataConflictError):
            store.write_batch([record_b_new, record_a_conflict])

        rows = raw_rows(db_path)
        assert len(rows) == 1
        assert rows[0][9] == "10"
    finally:
        store.close()


# J) same-batch conflict on an empty database leaves it empty


def test_same_batch_conflict_leaves_database_empty(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record_a = make_record(volume=Decimal(10))
        record_a_conflict = make_record(volume=Decimal(20))

        with pytest.raises(DataConflictError):
            store.write_batch([record_a, record_a_conflict])

        assert raw_rows(db_path) == []
    finally:
        store.close()


# K) invalid object mid-batch


def test_invalid_item_in_batch_raises_type_error_and_nothing_is_persisted(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        valid_record = make_record()
        with pytest.raises(TypeError):
            store.write_batch([valid_record, object()])

        assert raw_rows(db_path) == []
    finally:
        store.close()


# L) corrupted stored Decimal surfaces as DataCorruptionError and is not overwritten


def test_corrupted_stored_decimal_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    insert_raw_row(db_path, open="Infinity")

    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
            store.write_batch([make_record()])

        rows = raw_rows(db_path)
        assert len(rows) == 1
        assert rows[0][5] == "Infinity"
    finally:
        store.close()


# M) persistence across reopen


def test_written_data_persists_across_reopen(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.write_batch([make_record()])
    store.close()

    reopened = SQLiteHistoricalCandleStore(db_path)
    try:
        assert len(raw_rows(db_path)) == 1
    finally:
        reopened.close()


# N) store remains usable for a second write after a successful write


def test_store_remains_usable_for_a_second_write_after_a_successful_write(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        first = make_record(open_time=datetime(2024, 1, 1, tzinfo=UTC))
        second = make_record(open_time=datetime(2024, 1, 1, 1, tzinfo=UTC))
        store.write_batch([first])
        store.write_batch([second])
        assert len(raw_rows(db_path)) == 2
    finally:
        store.close()
