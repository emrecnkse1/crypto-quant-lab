"""SQLite implementation of the backend-neutral historical candle store.

This module only establishes the connection and initializes the canonical
schema (HISTORICAL_DATA_SPEC.md Bölüm 2, 14.2, 14.3). Reading and writing
candle data is not implemented yet.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from crypto_quant_lab.storage.base import HistoricalCandle

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


class SQLiteHistoricalCandleStore:
    """SQLite-backed implementation of the HistoricalCandleStore protocol (storage/base.py)."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        with self._connection:
            self._connection.execute(_CREATE_TABLE_SQL)

    def write_batch(self, records: Sequence[HistoricalCandle]) -> None:
        raise NotImplementedError("write_batch is not implemented yet")

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
