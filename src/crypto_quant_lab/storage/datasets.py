"""Candle dataset provenance and coverage value types (FUNDING_RESEARCH_SPEC.md Bölüm 10, 15).

A candle namespace is `(exchange, market_type, symbol, timeframe)` — the prefix
of the canonical candle key. `CandleDataset` binds exactly one immutable
provenance (`price_kind`, `source`) to one namespace, so contract-trade,
mark-price, index-price, continuous-contract and spot-trade candles can never
share a namespace unnoticed. `CandleCoverageInterval` records a half-open range
that a source was authoritatively and completely paginated over.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

BINANCE = "binance"
SPOT = "spot"
USDM_PERPETUAL = "usdm_perpetual"

SPOT_TRADE = "spot_trade"
CONTRACT_TRADE = "contract_trade"
MARK_PRICE = "mark_price"
INDEX_PRICE = "index_price"
CONTINUOUS_CONTRACT = "continuous_contract"

BINANCE_USDM_KLINES_SOURCE = "binance:GET https://fapi.binance.com/fapi/v1/klines"
BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE = (
    "binance:GET https://fapi.binance.com/fapi/v1/indexPriceKlines"
)


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


@dataclass(frozen=True, slots=True)
class CandleDataset:
    """Immutable provenance of one candle namespace."""

    exchange: str
    market_type: str
    symbol: str
    timeframe: str
    price_kind: str
    source: str

    def __post_init__(self) -> None:
        for field_name in (
            "exchange",
            "market_type",
            "symbol",
            "timeframe",
            "price_kind",
            "source",
        ):
            _require_identifier(getattr(self, field_name), field_name)

    @property
    def namespace(self) -> tuple[str, str, str, str]:
        return (self.exchange, self.market_type, self.symbol, self.timeframe)


@dataclass(frozen=True, slots=True)
class CandleCoverageInterval:
    """A `[start_time, end_time)` range completely paginated from the dataset's source."""

    start_time: datetime
    end_time: datetime

    def __post_init__(self) -> None:
        if datetime_to_epoch_us(self.start_time) >= datetime_to_epoch_us(self.end_time):
            raise ValueError(
                "start_time must be strictly before end_time, got "
                f"start_time={self.start_time!r}, end_time={self.end_time!r}"
            )


def binance_usdm_perpetual_contract_trade_dataset(symbol: str, timeframe: str) -> CandleDataset:
    """The canonical identity of Binance USDⓈ-M perpetual contract-trade klines."""
    return CandleDataset(
        exchange=BINANCE,
        market_type=USDM_PERPETUAL,
        symbol=symbol,
        timeframe=timeframe,
        price_kind=CONTRACT_TRADE,
        source=BINANCE_USDM_KLINES_SOURCE,
    )


def binance_usdm_index_price_dataset(pair: str, timeframe: str) -> CandleDataset:
    """The canonical identity of Binance USDⓈ-M index-price klines for `pair`.

    The namespace is `("binance", "usdm_perpetual", pair, timeframe)` — the
    same namespace as the pair's perpetual contract-trade klines, because the
    index is the USDⓈ-M futures index of that pair, not a separate market.
    `price_kind`/`source` differ, and a namespace holds exactly one
    provenance, so contract-trade and index-price candles can never share one
    physical store: index-price candles live in their own store (FUNDING_RESEARCH_SPEC.md
    Bölüm 15.3); writing both into one store fails with DataConflictError.
    """
    return CandleDataset(
        exchange=BINANCE,
        market_type=USDM_PERPETUAL,
        symbol=pair,
        timeframe=timeframe,
        price_kind=INDEX_PRICE,
        source=BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
    )


def coverage_contains(
    intervals: Sequence[CandleCoverageInterval], start_time: datetime, end_time: datetime
) -> bool:
    """Whether the union of `intervals` contains the whole `[start_time, end_time)`."""
    cursor = datetime_to_epoch_us(start_time)
    end_us = datetime_to_epoch_us(end_time)
    ordered = sorted(
        (datetime_to_epoch_us(item.start_time), datetime_to_epoch_us(item.end_time))
        for item in intervals
    )
    for interval_start, interval_end in ordered:
        if cursor >= end_us:
            break
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end)
    return cursor >= end_us
