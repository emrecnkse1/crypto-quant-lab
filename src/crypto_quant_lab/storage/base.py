"""Backend-neutral historical candle storage domain types and abstraction.

This module defines the canonical historical storage record, exception
hierarchy, and storage protocol (HISTORICAL_DATA_SPEC.md Bölüm 2, 8-11, 15).
It contains no backend-specific (e.g. SQLite) code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crypto_quant_lab.market_data.models import Candle


@dataclass(frozen=True, slots=True)
class HistoricalCandle:
    exchange: str
    market_type: str
    candle: Candle

    def __post_init__(self) -> None:
        if not self.exchange.strip():
            raise ValueError("exchange cannot be empty")
        if not self.market_type.strip():
            raise ValueError("market_type cannot be empty")

    @property
    def canonical_key(self) -> tuple[str, str, str, str, datetime]:
        """The 5-tuple that uniquely identifies this candle (spec Bölüm 2)."""
        return (
            self.exchange,
            self.market_type,
            self.candle.symbol,
            self.candle.timeframe,
            self.candle.open_time,
        )


class StorageError(Exception):
    """Base class for all backend-neutral historical storage errors."""


class DataConflictError(StorageError):
    """Same canonical key was written with a different OHLCV value (spec Bölüm 8.2)."""


class DataCorruptionError(StorageError):
    """A stored record could not be read back as valid data (spec Bölüm 12)."""


class HistoricalCandleStore(Protocol):
    """Backend-neutral contract for a historical candle store."""

    def write_batch(self, records: Sequence[HistoricalCandle]) -> None:
        """Write a batch of records atomically (spec Bölüm 10).

        The batch is either committed in full or not at all. A malformed
        record, a conflicting duplicate (DataConflictError), or any storage
        validation failure must roll back the entire batch.
        """
        ...

    def query(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistoricalCandle]:
        """Return candles for the given key over [start_time, end_time).

        - The time range is half-open: start_time is inclusive, end_time is
          exclusive.
        - Results are ordered by open_time ascending.
        - start_time and end_time must be timezone-aware.
        - start_time must be strictly less than end_time; otherwise the
          query is invalid.
        """
        ...

    def close(self) -> None:
        """Release any resources held by the store."""
        ...
