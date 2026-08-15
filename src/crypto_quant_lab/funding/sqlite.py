"""SQLite atomic events+coverage writer for funding storage (FUNDING_DATA_SPEC.md Bölüm 18-24, 31-34).

Faz 5B Microstep 6 boundary: this module implements fresh-database schema
creation for both funding tables and the complete atomic
`write_ingestion_batch` behavior only. `SQLiteHistoricalFundingStore` does
**not** yet implement `query_events`/`query_coverage` — it is intentionally
not yet a structurally complete `HistoricalFundingStore` (Protocol
conformance is not runtime-checked, and nothing consumes this class as that
Protocol yet). Public range queries and strict validation of a malformed
*pre-existing* schema are Microstep 7's responsibility; `CREATE TABLE IF NOT
EXISTS` here only guarantees a fresh or already-compatible database gets the
expected tables, not that a corrupted existing schema is detected.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from crypto_quant_lab.funding.models import HistoricalFundingEvent
from crypto_quant_lab.storage.base import DataConflictError, StorageError
from crypto_quant_lab.storage.sqlite_codec import (
    datetime_to_epoch_us,
    decimal_to_text,
    text_to_decimal,
)

_CREATE_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_funding_events (
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

_CREATE_COVERAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_funding_coverage (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_time_us INTEGER NOT NULL,
    end_time_us INTEGER NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, start_time_us, end_time_us)
)
"""

_SELECT_EXISTING_EVENT_SQL = """
SELECT funding_rate, reference_price
FROM historical_funding_events
WHERE exchange = ? AND market_type = ? AND symbol = ? AND event_time_us = ? AND rate_type = ?
"""

_INSERT_EVENT_SQL = """
INSERT INTO historical_funding_events (
    exchange, market_type, symbol, event_time_us, rate_type, funding_rate, reference_price
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_EXISTING_COVERAGE_SQL = """
SELECT 1
FROM historical_funding_coverage
WHERE exchange = ? AND market_type = ? AND symbol = ? AND start_time_us = ? AND end_time_us = ?
"""

_INSERT_COVERAGE_SQL = """
INSERT INTO historical_funding_coverage (
    exchange, market_type, symbol, start_time_us, end_time_us
) VALUES (?, ?, ?, ?, ?)
"""


def _require_str(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


class SQLiteHistoricalFundingStore:
    """SQLite-backed funding events+coverage writer (funding/store.py's `HistoricalFundingStore`).

    Microstep 6 only: implements `__init__` (schema creation),
    `write_ingestion_batch`, and `close`. `query_events`/`query_coverage`
    are deliberately absent until Microstep 7.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        try:
            with self._connection:
                self._connection.execute(_CREATE_EVENTS_TABLE_SQL)
                self._connection.execute(_CREATE_COVERAGE_TABLE_SQL)
        except sqlite3.Error as exc:
            self._connection.close()
            raise StorageError(f"unexpected SQLite error during initialization: {exc}") from exc

    def write_ingestion_batch(
        self,
        events: Sequence[HistoricalFundingEvent],
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        covered_start: datetime,
        covered_end: datetime,
    ) -> None:
        for event in events:
            if not isinstance(event, HistoricalFundingEvent):
                raise TypeError(
                    "events must contain HistoricalFundingEvent instances, got "
                    f"{type(event).__name__}"
                )

        _require_str(exchange, "exchange")
        _require_str(market_type, "market_type")
        _require_str(symbol, "symbol")

        covered_start_us = datetime_to_epoch_us(covered_start)
        covered_end_us = datetime_to_epoch_us(covered_end)
        if covered_start_us >= covered_end_us:
            raise ValueError(
                "covered_start must be strictly before covered_end, got "
                f"covered_start={covered_start!r}, covered_end={covered_end!r}"
            )

        event_times_us: list[int] = []
        for event in events:
            if (
                event.exchange != exchange
                or event.market_type != market_type
                or event.symbol != symbol
            ):
                raise ValueError(
                    "event partition must match the explicit exchange/market_type/symbol: "
                    f"event=({event.exchange!r}, {event.market_type!r}, {event.symbol!r}), "
                    f"explicit=({exchange!r}, {market_type!r}, {symbol!r})"
                )

            event_time_us = datetime_to_epoch_us(event.funding.event_time)
            if not (covered_start_us <= event_time_us < covered_end_us):
                raise ValueError(
                    "event.funding.event_time must satisfy covered_start <= event_time < "
                    f"covered_end: event_time={event.funding.event_time!r}, "
                    f"covered_start={covered_start!r}, covered_end={covered_end!r}"
                )
            event_times_us.append(event_time_us)

        try:
            with self._connection:
                for event, event_time_us in zip(events, event_times_us, strict=True):
                    self._write_event(event, event_time_us)
                self._write_coverage(
                    exchange, market_type, symbol, covered_start_us, covered_end_us
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"unexpected SQLite error during write_ingestion_batch: {exc}"
            ) from exc

    def _write_event(self, event: HistoricalFundingEvent, event_time_us: int) -> None:
        funding = event.funding
        key = (event.exchange, event.market_type, event.symbol, event_time_us, funding.rate_type)

        existing = self._connection.execute(_SELECT_EXISTING_EVENT_SQL, key).fetchone()

        if existing is not None:
            existing_funding_rate, existing_reference_price = (
                text_to_decimal(value) for value in existing
            )
            if (
                existing_funding_rate == funding.funding_rate
                and existing_reference_price == funding.reference_price
            ):
                return  # same canonical key, numerically identical payload: idempotent no-op
            raise DataConflictError(
                f"conflicting duplicate funding event for canonical key {key!r}"
            )

        self._connection.execute(
            _INSERT_EVENT_SQL,
            (
                *key,
                decimal_to_text(funding.funding_rate),
                decimal_to_text(funding.reference_price),
            ),
        )

    def _write_coverage(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        start_time_us: int,
        end_time_us: int,
    ) -> None:
        key = (exchange, market_type, symbol, start_time_us, end_time_us)

        existing = self._connection.execute(_SELECT_EXISTING_COVERAGE_SQL, key).fetchone()
        if existing is not None:
            return  # exact same coverage interval: idempotent no-op

        self._connection.execute(_INSERT_COVERAGE_SQL, key)

    def close(self) -> None:
        self._connection.close()
