"""SQLite funding store: atomic writes, strict schema validation, range queries.

(FUNDING_DATA_SPEC.md Bölüm 7, 18-24, 29-31, 38.)

`SQLiteHistoricalFundingStore` is now structurally complete against
`funding/store.py`'s `HistoricalFundingStore` Protocol: `write_ingestion_batch`
(Faz 5B Microstep 6), `query_events`/`query_coverage` (Microstep 7), and
`close`. Both funding tables are strictly validated on every construction —
`CREATE TABLE IF NOT EXISTS` alone never proves an existing table matches the
expected schema, so a dedicated `PRAGMA table_info`-based comparison (mirroring
`storage.sqlite.SQLiteHistoricalCandleStore._validate_schema`) runs
immediately after table creation for both tables. Query methods return only
mechanical, unmodified stored truth — no coverage union, no completeness
judgment, no clipping/merging; that remains a future pure quality layer's
responsibility, operating on the raw values these methods return.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from crypto_quant_lab.funding.models import (
    FundingCoverageInterval,
    FundingEvent,
    HistoricalFundingEvent,
)
from crypto_quant_lab.storage.base import DataConflictError, DataCorruptionError, StorageError
from crypto_quant_lab.storage.sqlite_codec import (
    datetime_to_epoch_us,
    decimal_to_text,
    epoch_us_to_datetime,
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

_SELECT_EVENTS_RANGE_SQL = """
SELECT event_time_us, rate_type, funding_rate, reference_price
FROM historical_funding_events
WHERE exchange = ? AND market_type = ? AND symbol = ?
  AND event_time_us >= ? AND event_time_us < ?
ORDER BY event_time_us ASC, rate_type ASC
"""

_SELECT_COVERAGE_OVERLAP_SQL = """
SELECT start_time_us, end_time_us
FROM historical_funding_coverage
WHERE exchange = ? AND market_type = ? AND symbol = ?
  AND end_time_us > ? AND start_time_us < ?
ORDER BY start_time_us ASC, end_time_us ASC
"""

# (name, declared type, notnull, pk position) per FUNDING_DATA_SPEC.md Bölüm 18, 20.
# pk position 0 means "not part of the primary key"; positions 1-5 give the
# composite key's declared order.
_EXPECTED_EVENTS_SCHEMA = (
    ("exchange", "TEXT", 1, 1),
    ("market_type", "TEXT", 1, 2),
    ("symbol", "TEXT", 1, 3),
    ("event_time_us", "INTEGER", 1, 4),
    ("rate_type", "TEXT", 1, 5),
    ("funding_rate", "TEXT", 1, 0),
    ("reference_price", "TEXT", 1, 0),
)

_EXPECTED_COVERAGE_SCHEMA = (
    ("exchange", "TEXT", 1, 1),
    ("market_type", "TEXT", 1, 2),
    ("symbol", "TEXT", 1, 3),
    ("start_time_us", "INTEGER", 1, 4),
    ("end_time_us", "INTEGER", 1, 5),
)


def _require_str(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


class SQLiteHistoricalFundingStore:
    """SQLite-backed implementation of `funding/store.py`'s `HistoricalFundingStore` protocol."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        try:
            try:
                with self._connection:
                    self._connection.execute(_CREATE_EVENTS_TABLE_SQL)
                    self._connection.execute(_CREATE_COVERAGE_TABLE_SQL)
            except sqlite3.Error as exc:
                raise StorageError(f"unexpected SQLite error during initialization: {exc}") from exc

            self._validate_table_schema("historical_funding_events", _EXPECTED_EVENTS_SCHEMA)
            self._validate_table_schema("historical_funding_coverage", _EXPECTED_COVERAGE_SCHEMA)
        except Exception:
            self._connection.close()
            raise

    def _validate_table_schema(
        self, table_name: str, expected_schema: tuple[tuple[str, str, int, int], ...]
    ) -> None:
        try:
            rows = self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        except sqlite3.Error as exc:
            raise StorageError(
                f"unexpected SQLite error during schema validation of {table_name!r}: {exc}"
            ) from exc

        # PRAGMA table_info rows: (cid, name, type, notnull, dflt_value, pk).
        actual_columns = [
            (row[1], row[2], row[3], row[5]) for row in sorted(rows, key=lambda row: row[0])
        ]

        if len(actual_columns) != len(expected_schema):
            raise DataCorruptionError(
                f"{table_name} schema mismatch: unexpected column count, "
                f"expected {len(expected_schema)} columns "
                f"{[c[0] for c in expected_schema]!r}, "
                f"found {len(actual_columns)} columns {[c[0] for c in actual_columns]!r}"
            )

        for position, (expected, actual) in enumerate(
            zip(expected_schema, actual_columns, strict=True)
        ):
            expected_name, expected_type, expected_notnull, expected_pk = expected
            actual_name, actual_type, actual_notnull, actual_pk = actual

            if actual_name != expected_name:
                raise DataCorruptionError(
                    f"{table_name} schema mismatch at column position {position}: "
                    f"unexpected column name {actual_name!r}, expected {expected_name!r}"
                )
            if actual_type != expected_type:
                raise DataCorruptionError(
                    f"{table_name} schema mismatch for column {actual_name!r}: "
                    f"unexpected type {actual_type!r}, expected {expected_type!r}"
                )
            if actual_notnull != expected_notnull:
                raise DataCorruptionError(
                    f"{table_name} schema mismatch for column {actual_name!r}: "
                    f"unexpected NOT NULL flag {actual_notnull!r}, expected {expected_notnull!r}"
                )
            if actual_pk != expected_pk:
                raise DataCorruptionError(
                    f"{table_name} schema mismatch for column {actual_name!r}: "
                    f"unexpected primary key position {actual_pk!r}, expected {expected_pk!r}"
                )

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

    def query_events(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistoricalFundingEvent]:
        _require_str(exchange, "exchange")
        _require_str(market_type, "market_type")
        _require_str(symbol, "symbol")

        start_us = datetime_to_epoch_us(start_time)
        end_us = datetime_to_epoch_us(end_time)
        if start_us >= end_us:
            raise ValueError(
                "start_time must be strictly before end_time, got "
                f"start_time={start_time!r}, end_time={end_time!r}"
            )

        try:
            rows = self._connection.execute(
                _SELECT_EVENTS_RANGE_SQL, (exchange, market_type, symbol, start_us, end_us)
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"unexpected SQLite error during query_events: {exc}") from exc

        return [self._row_to_event(exchange, market_type, symbol, row) for row in rows]

    @staticmethod
    def _row_to_event(
        exchange: str, market_type: str, symbol: str, row: tuple
    ) -> HistoricalFundingEvent:
        event_time_us, rate_type, funding_rate, reference_price = row
        try:
            funding = FundingEvent(
                event_time=epoch_us_to_datetime(event_time_us),
                funding_rate=text_to_decimal(funding_rate),
                reference_price=text_to_decimal(reference_price),
                rate_type=rate_type,
            )
        except (TypeError, ValueError) as exc:
            raise DataCorruptionError(f"stored funding event failed to reconstruct: {exc}") from exc

        return HistoricalFundingEvent(
            exchange=exchange, market_type=market_type, symbol=symbol, funding=funding
        )

    def query_coverage(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingCoverageInterval]:
        _require_str(exchange, "exchange")
        _require_str(market_type, "market_type")
        _require_str(symbol, "symbol")

        start_us = datetime_to_epoch_us(start_time)
        end_us = datetime_to_epoch_us(end_time)
        if start_us >= end_us:
            raise ValueError(
                "start_time must be strictly before end_time, got "
                f"start_time={start_time!r}, end_time={end_time!r}"
            )

        try:
            rows = self._connection.execute(
                _SELECT_COVERAGE_OVERLAP_SQL, (exchange, market_type, symbol, start_us, end_us)
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"unexpected SQLite error during query_coverage: {exc}") from exc

        return [self._row_to_coverage(row) for row in rows]

    @staticmethod
    def _row_to_coverage(row: tuple) -> FundingCoverageInterval:
        start_time_us, end_time_us = row
        try:
            return FundingCoverageInterval(
                start_time=epoch_us_to_datetime(start_time_us),
                end_time=epoch_us_to_datetime(end_time_us),
            )
        except (TypeError, ValueError) as exc:
            raise DataCorruptionError(
                f"stored funding coverage interval failed to reconstruct: {exc}"
            ) from exc

    def close(self) -> None:
        self._connection.close()
