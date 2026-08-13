import sqlite3
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import DataCorruptionError, HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

_COLUMNS_SQL = (
    "exchange, market_type, symbol, timeframe, open_time_us, open, high, low, close, volume"
)


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


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


def row_count(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("SELECT COUNT(*) FROM historical_candles").fetchone()[0]
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


# A) empty database -> []


def test_query_on_empty_database_returns_empty_list(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert result == []
    finally:
        store.close()


# B) single matching candle query


def test_single_matching_candle_is_returned(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record = make_record()
        store.write_batch([record])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0] == record
    finally:
        store.close()


# C) multiple candles returned in chronological ascending order


def test_multiple_candles_are_returned_in_ascending_open_time_order(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record_late = make_record(open_time=datetime(2024, 1, 1, 2, tzinfo=UTC))
        record_early = make_record(open_time=datetime(2024, 1, 1, 0, tzinfo=UTC))
        record_mid = make_record(open_time=datetime(2024, 1, 1, 1, tzinfo=UTC))

        # written intentionally out of chronological order
        store.write_batch([record_late, record_early, record_mid])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert [r.candle.open_time for r in result] == [
            record_early.candle.open_time,
            record_mid.candle.open_time,
            record_late.candle.open_time,
        ]
    finally:
        store.close()


# D) start boundary inclusive


def test_start_boundary_is_inclusive(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        boundary_time = datetime(2024, 1, 1, tzinfo=UTC)
        record = make_record(open_time=boundary_time)
        store.write_batch([record])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            boundary_time,
            boundary_time + timedelta(hours=1),
        )

        assert len(result) == 1
        assert result[0].candle.open_time == boundary_time
    finally:
        store.close()


# E) end boundary exclusive


def test_end_boundary_is_exclusive(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        boundary_time = datetime(2024, 1, 1, 1, tzinfo=UTC)
        record = make_record(open_time=boundary_time)
        store.write_batch([record])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            boundary_time,
        )

        assert result == []
    finally:
        store.close()


# F) correct exchange filter


def test_query_filters_by_exchange(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(exchange="binance")])
        store.write_batch([make_record(exchange="other_exchange")])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].exchange == "binance"
    finally:
        store.close()


# G) correct market_type filter


def test_query_filters_by_market_type(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(market_type="spot")])
        store.write_batch([make_record(market_type="futures")])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].market_type == "spot"
    finally:
        store.close()


# H) correct symbol filter


def test_query_filters_by_symbol(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(symbol="BTCUSDT")])
        store.write_batch([make_record(symbol="ETHUSDT")])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].candle.symbol == "BTCUSDT"
    finally:
        store.close()


# I) correct timeframe filter


def test_query_filters_by_timeframe(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record(timeframe="1h")])
        store.write_batch([make_record(timeframe="4h")])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].candle.timeframe == "1h"
    finally:
        store.close()


# J) exact Decimal reconstruction, trailing zeros included


def test_decimal_values_round_trip_exactly_including_trailing_zeros(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        record = make_record(
            open=Decimal("100.12345678"),
            high=Decimal("110.00"),
            low=Decimal("90.100"),
            close=Decimal("105.5"),
            volume=Decimal("10.00000000"),
        )
        store.write_batch([record])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        candle = result[0].candle
        assert candle.open == record.candle.open
        assert candle.open.as_tuple() == record.candle.open.as_tuple()
        assert str(candle.high) == "110.00"
        assert str(candle.low) == "90.100"
        assert str(candle.volume) == "10.00000000"
    finally:
        store.close()


# K) returned open_time timezone-aware UTC


def test_returned_open_time_is_timezone_aware_utc(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record()])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        assert len(result) == 1
        open_time = result[0].candle.open_time
        assert open_time.tzinfo is not None
        assert open_time.utcoffset() is not None
        assert open_time.utcoffset() == timedelta(0)
    finally:
        store.close()


# L) non-UTC aware start/end normalize to same UTC instant


def test_non_utc_boundaries_normalize_to_same_utc_instant(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        boundary_time = datetime(2024, 1, 1, 1, tzinfo=UTC)
        record = make_record(open_time=boundary_time)
        store.write_batch([record])

        plus_five = timezone(timedelta(hours=5))
        start_local = (boundary_time - timedelta(hours=1)).astimezone(plus_five)
        end_local = (boundary_time + timedelta(hours=1)).astimezone(plus_five)

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            start_local,
            end_local,
        )

        assert len(result) == 1
        assert result[0].candle.open_time == boundary_time
    finally:
        store.close()


# M) naive start_time rejected


def test_naive_start_time_is_rejected(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.query(
                "binance",
                "spot",
                "BTCUSDT",
                "1h",
                datetime(2024, 1, 1),  # noqa: DTZ001
                datetime(2024, 1, 2, tzinfo=UTC),
            )
    finally:
        store.close()


# N) naive end_time rejected


def test_naive_end_time_is_rejected(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.query(
                "binance",
                "spot",
                "BTCUSDT",
                "1h",
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 2),  # noqa: DTZ001
            )
    finally:
        store.close()


# O) pseudo-naive boundary rejected


def test_pseudo_naive_boundary_is_rejected(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.query(
                "binance",
                "spot",
                "BTCUSDT",
                "1h",
                datetime(2024, 1, 1, tzinfo=_BrokenTzInfo()),
                datetime(2024, 1, 2, tzinfo=UTC),
            )
    finally:
        store.close()


# P) start_time == end_time -> ValueError


def test_equal_start_and_end_time_raises_value_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        same_time = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError):
            store.query("binance", "spot", "BTCUSDT", "1h", same_time, same_time)
    finally:
        store.close()


# Q) start_time > end_time -> ValueError


def test_start_time_after_end_time_raises_value_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(ValueError):
            store.query(
                "binance",
                "spot",
                "BTCUSDT",
                "1h",
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 1, 1, tzinfo=UTC),
            )
    finally:
        store.close()


# R) corrupted stored Decimal -> DataCorruptionError


def test_corrupted_stored_decimal_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    insert_raw_row(db_path, open="not-a-decimal")

    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
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


def test_corrupted_stored_infinity_decimal_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    insert_raw_row(db_path, high="Infinity")

    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
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


# S) corrupted/out-of-range stored open_time_us -> DataCorruptionError
#
# A magnitude large enough to overflow epoch_us_to_datetime's datetime
# arithmetic can never fall inside any valid [start_time, end_time) range,
# because valid datetimes only span year 1..9999 and any such range's
# epoch-microsecond bounds are themselves always convertible back. The
# reachable "out-of-range" corruption is therefore a SQLite type deviation:
# a fractional REAL value in the INTEGER open_time_us column (which SQLite
# keeps as REAL rather than losslessly coercing to INTEGER), surfacing as a
# Python float that epoch_us_to_datetime's isinstance(value, int) check
# rejects with TypeError.


def test_type_corrupted_stored_open_time_raises_data_corruption_error(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    store.close()

    insert_raw_row(db_path, open_time_us=1704067200000000.5)

    store = SQLiteHistoricalCandleStore(db_path)
    try:
        with pytest.raises(DataCorruptionError):
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


# T) query does not disturb store usability; read-only behavior (row count unchanged)


def test_query_is_read_only_and_store_remains_usable_afterward(tmp_path):
    db_path = tmp_path / "historical.db"
    store = SQLiteHistoricalCandleStore(db_path)
    try:
        store.write_batch([make_record()])
        count_before = row_count(db_path)

        store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        count_after = row_count(db_path)
        assert count_after == count_before

        # store remains usable for further writes and queries after a query call
        second_record = make_record(open_time=datetime(2024, 1, 1, 1, tzinfo=UTC))
        store.write_batch([second_record])

        result = store.query(
            "binance",
            "spot",
            "BTCUSDT",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(result) == 2
    finally:
        store.close()
