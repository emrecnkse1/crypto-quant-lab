"""Binance funding history -> canonical `HistoricalFundingStore` ingestion bridge.

(FUNDING_DATA_SPEC.md Bölüm 33, Microstep 10: canonical mapping + half-open /
`source_as_of` validation + atomic ingestion bridge)

Composes, in locked pipeline order (Bölüm 33 step 8-11): raw Binance record
-> canonical `HistoricalFundingEvent` mapping, exact canonical
`[requested_start, requested_end)` filtering, `source_as_of` settled-history
validation on the surviving events, then exactly one atomic
`store.write_ingestion_batch()` call. No DB write happens before every prior
stage has fully succeeded.

Reuses `funding.binance_pagination.fetch_binance_funding_history` unmodified
for pagination + bounded `ConnectionError`-only retry (Microstep 9) and
`data_quality.transport.transport_start_ms`/`transport_end_ms` for safe
floor/ceil Binance-inclusive transport-boundary derivation — no second retry
layer, no duplicated pagination/tie-safety logic, no duplicated ms-boundary
arithmetic.

No funding-quality PASS/FAIL logic, no coverage-union computation, no
`FundingModel`, no accounting/replay, no candle/OHLCV dependency, and no
wall-clock read (`datetime.now()`/`utcnow()`/`time.time()`) happen here — all
temporal authority is explicit caller input. Funding quality remains a
future Microstep 11 concern, operating purely on what this module persists.
"""

from collections.abc import Callable
from datetime import datetime

from crypto_quant_lab.data_quality.transport import transport_end_ms, transport_start_ms
from crypto_quant_lab.funding.binance import BinanceHistoricalFundingRecord
from crypto_quant_lab.funding.binance_pagination import fetch_binance_funding_history
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_EXCHANGE = "binance"

FetchFundingHistory = Callable[..., list[BinanceHistoricalFundingRecord]]


def _validate_str_field(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty str, got {value!r}")
    return value


def _map_to_canonical(
    raw: BinanceHistoricalFundingRecord, *, market_type: str, symbol: str
) -> HistoricalFundingEvent:
    funding = FundingEvent(
        event_time=raw.funding_time,
        funding_rate=raw.funding_rate,
        reference_price=raw.mark_price,
        rate_type=raw.rate_type,
    )
    return HistoricalFundingEvent(
        exchange=_EXCHANGE, market_type=market_type, symbol=symbol, funding=funding
    )


def ingest_binance_historical_funding_range(
    store: HistoricalFundingStore,
    *,
    symbol: str,
    market_type: str,
    requested_start: datetime,
    requested_end: datetime,
    source_as_of: datetime,
    ingestion_as_of: datetime,
    timeout: float = 10.0,
    max_attempts: int = 3,
    fetch_history: FetchFundingHistory | None = None,
) -> list[HistoricalFundingEvent]:
    """Ingest settled Binance funding history over `[requested_start, requested_end)`.

    `exchange` is always `"binance"` (this bridge only ever talks to the
    Binance source; a caller cannot mislabel Binance data as another
    exchange). `market_type` is required, caller-supplied, exact, and
    unclassified by this module (FUNDING_DATA_SPEC.md Bölüm 38 deliberately
    locks no global enum here).

    `source_as_of` must satisfy `source_as_of >= requested_end` — the source
    snapshot cannot be authoritative over a canonical interval whose tail it
    could not yet know, and coverage is never written otherwise. This is
    checked before any network request. Every surviving canonical event is
    additionally guarded by the explicit per-event `event_time > source_as_of`
    rejection (FUNDING_DATA_SPEC.md Bölüm 29) — mathematically unreachable
    for valid inputs once the interval-level guard holds, but kept as
    defense-in-depth per spec, not removed as redundant.

    `ingestion_as_of` is validated only for genuineness (aware,
    non-pseudo-naive) — v1 locks no further relationship to `source_as_of`
    and never persists it (no `ingested_at`/wall-clock storage column
    exists).

    A successful empty result (zero source events, or all fetched events
    filtered out as expected inclusive-transport overfetch) is not a
    failure: `store.write_ingestion_batch` is still called exactly once,
    recording coverage over the full canonical interval either way
    (FUNDING_DATA_SPEC.md Bölüm 35) — unlike the candle ingestion bridge,
    this function never early-returns on an empty result.
    """
    validated_symbol = _validate_str_field(symbol, "symbol")
    validated_market_type = _validate_str_field(market_type, "market_type")

    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)
    if requested_start_us >= requested_end_us:
        raise ValueError(
            "requested_start must be strictly before requested_end, got "
            f"requested_start={requested_start!r}, requested_end={requested_end!r}"
        )

    source_as_of_us = datetime_to_epoch_us(source_as_of)
    if source_as_of_us < requested_end_us:
        raise ValueError(
            "source_as_of must be >= requested_end, got "
            f"source_as_of={source_as_of!r}, requested_end={requested_end!r}"
        )

    datetime_to_epoch_us(ingestion_as_of)  # validates genuine, aware, non-pseudo-naive

    transport_start = transport_start_ms(requested_start)
    transport_end = transport_end_ms(requested_end)

    raw_fetch = fetch_history if fetch_history is not None else fetch_binance_funding_history
    raw_records = raw_fetch(
        validated_symbol, transport_start, transport_end, timeout=timeout, max_attempts=max_attempts
    )

    filtered_events: list[HistoricalFundingEvent] = []
    for raw in raw_records:
        event = _map_to_canonical(raw, market_type=validated_market_type, symbol=validated_symbol)
        event_time_us = datetime_to_epoch_us(event.funding.event_time)

        if not (requested_start_us <= event_time_us < requested_end_us):
            continue  # expected inclusive-transport overfetch, not source corruption

        if event_time_us > source_as_of_us:
            raise ValueError(
                "canonical event_time exceeds source_as_of: "
                f"event_time={event.funding.event_time!r}, source_as_of={source_as_of!r}"
            )

        filtered_events.append(event)

    store.write_ingestion_batch(
        filtered_events,
        exchange=_EXCHANGE,
        market_type=validated_market_type,
        symbol=validated_symbol,
        covered_start=requested_start,
        covered_end=requested_end,
    )

    return filtered_events
