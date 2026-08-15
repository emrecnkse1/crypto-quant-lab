"""Canonical funding domain model (FUNDING_SPEC.md Bölüm 3; FUNDING_DATA_SPEC.md Bölüm 3-8).

`FundingEvent` is a pure economic/history payload — no exchange, market_type,
or symbol knowledge. It is exchange-neutral: `rate_type` is an opaque,
required, non-empty discriminator, never restricted to any specific source
vocabulary (e.g. Binance's `Regular`/`Special`) at this canonical layer —
that strictness belongs to a future source-specific adapter/parser, not this
class (FUNDING_DATA_SPEC.md Bölüm 4).

`HistoricalFundingEvent` adds the partition/identity context
(`exchange`/`market_type`/`symbol`), mirroring `Candle`/`HistoricalCandle`'s
existing separation. Its `canonical_key` is the locked v1 fail-closed
identity (FUNDING_DATA_SPEC.md Bölüm 5) — it intentionally excludes
`funding_rate`/`reference_price`, which are payload values used for
idempotency/conflict comparison later, not identity.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


def _require_str(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


@dataclass(frozen=True, slots=True)
class FundingEvent:
    """A single settled funding settlement payload (FUNDING_DATA_SPEC.md Bölüm 3).

    `rate_type` is preserved exactly as supplied — never stripped, upper/
    lowercased, or otherwise normalized (FUNDING_DATA_SPEC.md Bölüm 39).
    """

    event_time: datetime
    funding_rate: Decimal
    reference_price: Decimal
    rate_type: str

    def __post_init__(self) -> None:
        datetime_to_epoch_us(self.event_time)  # validates genuine, aware, non-pseudo-naive

        _require_decimal(self.funding_rate, "funding_rate")
        if not self.funding_rate.is_finite():
            raise ValueError(f"funding_rate must be finite, got {self.funding_rate}")

        _require_decimal(self.reference_price, "reference_price")
        if not self.reference_price.is_finite():
            raise ValueError(f"reference_price must be finite, got {self.reference_price}")
        if self.reference_price <= 0:
            raise ValueError(f"reference_price must be > 0, got {self.reference_price}")

        _require_str(self.rate_type, "rate_type")


@dataclass(frozen=True, slots=True)
class HistoricalFundingEvent:
    """A `FundingEvent` with its partition/identity context (FUNDING_DATA_SPEC.md Bölüm 3)."""

    exchange: str
    market_type: str
    symbol: str
    funding: FundingEvent

    def __post_init__(self) -> None:
        _require_str(self.exchange, "exchange")
        _require_str(self.market_type, "market_type")
        _require_str(self.symbol, "symbol")
        if not isinstance(self.funding, FundingEvent):
            raise TypeError(f"funding must be a FundingEvent, got {type(self.funding).__name__}")

    @property
    def canonical_key(self) -> tuple[str, str, str, datetime, str]:
        """The locked v1 fail-closed identity 5-tuple (FUNDING_DATA_SPEC.md Bölüm 5)."""
        return (
            self.exchange,
            self.market_type,
            self.symbol,
            self.funding.event_time,
            self.funding.rate_type,
        )


@dataclass(frozen=True, slots=True)
class FundingCoverageInterval:
    """A validated `[start_time, end_time)` coverage range (FUNDING_DATA_SPEC.md Bölüm 19-20).

    A pure time-range value — no `exchange`/`market_type`/`symbol` and no
    `canonical_key`: partition identity is already fixed by the store query
    that returns this value, never carried redundantly on the value itself.
    """

    start_time: datetime
    end_time: datetime

    def __post_init__(self) -> None:
        datetime_to_epoch_us(self.start_time)  # validates genuine, aware, non-pseudo-naive
        datetime_to_epoch_us(self.end_time)

        if self.start_time >= self.end_time:
            raise ValueError(
                f"start_time must be strictly before end_time, got "
                f"start_time={self.start_time!r}, end_time={self.end_time!r}"
            )
