"""SQLite implementation of the backend-neutral historical candle store.

Establishes the connection, initializes the canonical schema, and implements
atomic, conflict-safe batch writes and half-open range queries
(HISTORICAL_DATA_SPEC.md Bölüm 2, 8, 9, 10, 11, 14.2, 14.3).
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import (
    DataConflictError,
    DataCorruptionError,
    HistoricalCandle,
    StorageError,
)
from crypto_quant_lab.storage.sqlite_codec import (
    datetime_to_epoch_us,
    decimal_to_text,
    epoch_us_to_datetime,
    text_to_decimal,
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_candles (
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

_SELECT_EXISTING_SQL = """
SELECT open, high, low, close, volume
FROM historical_candles
WHERE exchange = ? AND market_type = ? AND symbol = ? AND timeframe = ? AND open_time_us = ?
"""

_INSERT_SQL = """
INSERT INTO historical_candles (
    exchange, market_type, symbol, timeframe, open_time_us,
    open, high, low, close, volume
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_RANGE_SQL = """
SELECT open_time_us, open, high, low, close, volume
FROM historical_candles
WHERE exchange = ? AND market_type = ? AND symbol = ? AND timeframe = ?
  AND open_time_us >= ? AND open_time_us < ?
ORDER BY open_time_us ASC
"""


class SQLiteHistoricalCandleStore:
    """SQLite-backed implementation of the HistoricalCandleStore protocol (storage/base.py)."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        with self._connection:
            self._connection.execute(_CREATE_TABLE_SQL)

    def write_batch(self, records: Sequence[HistoricalCandle]) -> None:
        for record in records:
            if not isinstance(record, HistoricalCandle):
                raise TypeError(
                    f"records must contain HistoricalCandle instances, got {type(record).__name__}"
                )

        if not records:
            return

        try:
            with self._connection:
                for record in records:
                    self._write_one(record)
        except sqlite3.Error as exc:
            raise StorageError(f"unexpected SQLite error during write_batch: {exc}") from exc

    def _write_one(self, record: HistoricalCandle) -> None:
        exchange = record.exchange
        market_type = record.market_type
        candle = record.candle
        open_time_us = datetime_to_epoch_us(candle.open_time)
        key = (exchange, market_type, candle.symbol, candle.timeframe, open_time_us)

        existing = self._connection.execute(_SELECT_EXISTING_SQL, key).fetchone()

        if existing is not None:
            existing_open, existing_high, existing_low, existing_close, existing_volume = (
                text_to_decimal(value) for value in existing
            )
            if (
                existing_open == candle.open
                and existing_high == candle.high
                and existing_low == candle.low
                and existing_close == candle.close
                and existing_volume == candle.volume
            ):
                return  # same canonical key, numerically identical OHLCV: idempotent no-op
            raise DataConflictError(f"conflicting duplicate for canonical key {key!r}")

        self._connection.execute(
            _INSERT_SQL,
            (
                *key,
                decimal_to_text(candle.open),
                decimal_to_text(candle.high),
                decimal_to_text(candle.low),
                decimal_to_text(candle.close),
                decimal_to_text(candle.volume),
            ),
        )

    def query(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistoricalCandle]:
        start_us = datetime_to_epoch_us(start_time)
        end_us = datetime_to_epoch_us(end_time)
        if start_us >= end_us:
            raise ValueError(
                "start_time must be strictly less than end_time, got "
                f"start_time={start_time!r}, end_time={end_time!r}"
            )

        try:
            rows = self._connection.execute(
                _SELECT_RANGE_SQL,
                (exchange, market_type, symbol, timeframe, start_us, end_us),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"unexpected SQLite error during query: {exc}") from exc

        return [self._row_to_record(exchange, market_type, symbol, timeframe, row) for row in rows]

    @staticmethod
    def _row_to_record(
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        row: tuple,
    ) -> HistoricalCandle:
        open_time_us, open_, high, low, close, volume = row
        try:
            candle = Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=epoch_us_to_datetime(open_time_us),
                open=text_to_decimal(open_),
                high=text_to_decimal(high),
                low=text_to_decimal(low),
                close=text_to_decimal(close),
                volume=text_to_decimal(volume),
            )
        except (TypeError, ValueError) as exc:
            raise DataCorruptionError(f"stored row failed to reconstruct: {exc}") from exc

        return HistoricalCandle(exchange=exchange, market_type=market_type, candle=candle)

    def close(self) -> None:
        self._connection.close()
