"""SQLite implementation of the backend-neutral historical candle store.

Establishes the connection, initializes the canonical schema, and implements
atomic, conflict-safe batch writes (HISTORICAL_DATA_SPEC.md Bölüm 2, 8, 9,
10, 14.2, 14.3). Reading (query) is not implemented yet.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from crypto_quant_lab.storage.base import (
    DataConflictError,
    HistoricalCandle,
    StorageError,
)
from crypto_quant_lab.storage.sqlite_codec import (
    datetime_to_epoch_us,
    decimal_to_text,
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
        raise NotImplementedError("query is not implemented yet")

    def close(self) -> None:
        self._connection.close()
