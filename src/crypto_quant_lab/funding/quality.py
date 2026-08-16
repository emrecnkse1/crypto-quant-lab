"""Funding data quality: coverage-union completeness + event structural validation.

(FUNDING_DATA_SPEC.md Bölüm 25-30, Microstep 11: "→ Faz 5B data/storage
acceptance checkpoint")

Dedicated funding quality path — deliberately does **not** reuse the candle
`data_quality` package's dense-grid logic (expected timestamp grid, fixed
cadence, synthetic gap, interpolation, fill-forward): none of that applies to
funding (Bölüm 26). Funding quality is coverage-driven instead: PASS requires
the requested canonical `[requested_start, requested_end)` interval to be
fully covered by the union of authoritative, persisted coverage intervals,
and every queried event to be structurally valid — `event_count` may be zero
and still PASS (a known, authoritative zero-event history), while an
uncovered range FAILs regardless of what events happen to be present
(Bölüm 26, 27).

Pure computation only: no network, no Binance/ingestion imports, no
wall-clock read, no coverage repair/merge/persistence. Contract violations
by an upstream dependency (wrong object types, partition mismatches,
out-of-range/out-of-order events, non-overlapping or misordered coverage
rows) are raised as exceptions immediately — they are not encoded as a
`"FAIL"` status, which is reserved for legitimate coverage incompleteness.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from crypto_quant_lab.funding.models import FundingCoverageInterval, HistoricalFundingEvent
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_ALLOWED_STATUSES = frozenset({"PASS", "FAIL"})
_MAX_SAMPLES = 20


def _require_str(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")


@dataclass(frozen=True, slots=True)
class FundingDataQualityReport:
    """Passive result of a funding coverage-union + event-validation evaluation.

    Pure data container — no computation happens here; `__post_init__` only
    validates internal consistency of already-computed values.
    """

    exchange: str
    market_type: str
    symbol: str

    requested_start: datetime
    requested_end: datetime

    event_count: int

    coverage_gap_count: int
    coverage_gaps: tuple[tuple[datetime, datetime], ...]

    overall_status: str

    def __post_init__(self) -> None:
        _require_str(self.exchange, "exchange")
        _require_str(self.market_type, "market_type")
        _require_str(self.symbol, "symbol")

        start_us = datetime_to_epoch_us(self.requested_start)
        end_us = datetime_to_epoch_us(self.requested_end)
        if start_us >= end_us:
            raise ValueError(
                "requested_start must be strictly before requested_end, got "
                f"requested_start={self.requested_start!r}, requested_end={self.requested_end!r}"
            )

        for field_name in ("event_count", "coverage_gap_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value}")

        if not isinstance(self.coverage_gaps, tuple):
            raise TypeError(
                f"coverage_gaps must be a tuple, got {type(self.coverage_gaps).__name__}"
            )
        if len(self.coverage_gaps) > _MAX_SAMPLES:
            raise ValueError(
                f"coverage_gaps must contain at most {_MAX_SAMPLES} samples, "
                f"got {len(self.coverage_gaps)}"
            )
        for gap in self.coverage_gaps:
            if not (isinstance(gap, tuple) and len(gap) == 2):
                raise TypeError(f"each coverage_gaps entry must be a 2-tuple, got {gap!r}")
            gap_start, gap_end = gap
            if datetime_to_epoch_us(gap_start) >= datetime_to_epoch_us(gap_end):
                raise ValueError(f"each coverage_gaps entry must satisfy start < end, got {gap!r}")

        if self.overall_status not in _ALLOWED_STATUSES:
            raise ValueError(
                f"overall_status must be one of {sorted(_ALLOWED_STATUSES)}, "
                f"got {self.overall_status!r}"
            )


def _validate_str_field(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty str, got {value!r}")
    return value


def _validate_events(
    events: Sequence[HistoricalFundingEvent],
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    requested_start: datetime,
    requested_end: datetime,
    requested_start_us: int,
    requested_end_us: int,
) -> None:
    """Validate type, partition, range, ordering, and canonical-key uniqueness.

    Raises immediately on the first violation — these are contract
    violations by the caller/store, never a quality FAIL.
    """
    seen_keys: set[tuple[str, str, str, datetime, str]] = set()
    previous_sort_key: tuple[int, str] | None = None

    for event in events:
        if not isinstance(event, HistoricalFundingEvent):
            raise TypeError(
                f"events must contain HistoricalFundingEvent instances, got {type(event).__name__}"
            )
        if event.exchange != exchange or event.market_type != market_type or event.symbol != symbol:
            raise ValueError(
                "event partition must match the requested exchange/market_type/symbol: "
                f"event=({event.exchange!r}, {event.market_type!r}, {event.symbol!r}), "
                f"requested=({exchange!r}, {market_type!r}, {symbol!r})"
            )

        event_time_us = datetime_to_epoch_us(event.funding.event_time)
        if not (requested_start_us <= event_time_us < requested_end_us):
            raise ValueError(
                "event.funding.event_time is outside the requested "
                "[requested_start, requested_end) range: "
                f"event_time={event.funding.event_time!r}, requested_start={requested_start!r}, "
                f"requested_end={requested_end!r}"
            )

        sort_key = (event_time_us, event.funding.rate_type)
        if previous_sort_key is not None and sort_key < previous_sort_key:
            raise ValueError(
                "events are not ordered (event_time, rate_type) ascending: "
                f"{sort_key!r} follows {previous_sort_key!r}"
            )
        previous_sort_key = sort_key

        key = event.canonical_key
        if key in seen_keys:
            raise ValueError(f"duplicate canonical event key: {key!r}")
        seen_keys.add(key)


def _compute_coverage_gaps(
    coverage_intervals: Sequence[FundingCoverageInterval],
    *,
    requested_start: datetime,
    requested_end: datetime,
    requested_start_us: int,
    requested_end_us: int,
) -> list[tuple[datetime, datetime]]:
    """Validate coverage rows and compute the half-open union's missing sub-intervals.

    Every interval must genuinely overlap the requested range and the
    sequence must already be ordered `(start_time, end_time)` ascending
    (the store's own promised ordering) — violations raise immediately.
    Clipping to the requested range happens only in-memory, for this
    computation; nothing is mutated or persisted.
    """
    gaps: list[tuple[datetime, datetime]] = []
    cursor = requested_start
    previous_pair: tuple[int, int] | None = None

    for interval in coverage_intervals:
        if not isinstance(interval, FundingCoverageInterval):
            raise TypeError(
                "coverage_intervals must contain FundingCoverageInterval instances, got "
                f"{type(interval).__name__}"
            )

        start_us = datetime_to_epoch_us(interval.start_time)
        end_us = datetime_to_epoch_us(interval.end_time)
        if not (end_us > requested_start_us and start_us < requested_end_us):
            raise ValueError(
                "coverage interval does not overlap the requested range: "
                f"interval=({interval.start_time!r}, {interval.end_time!r}), "
                f"requested=({requested_start!r}, {requested_end!r})"
            )

        pair = (start_us, end_us)
        if previous_pair is not None and pair < previous_pair:
            raise ValueError(
                "coverage_intervals are not ordered (start_time, end_time) ascending: "
                f"interval={interval!r}"
            )
        previous_pair = pair

        clipped_start = max(interval.start_time, requested_start)
        clipped_end = min(interval.end_time, requested_end)

        if clipped_start > cursor:
            gaps.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)

    if cursor < requested_end:
        gaps.append((cursor, requested_end))

    return gaps


def build_funding_data_quality_report(
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    requested_start: datetime,
    requested_end: datetime,
    events: Sequence[HistoricalFundingEvent],
    coverage_intervals: Sequence[FundingCoverageInterval],
) -> FundingDataQualityReport:
    """Compute a `FundingDataQualityReport` over `[requested_start, requested_end)`.

    Pure: no DB, no network, no wall clock. `events`/`coverage_intervals`
    must already be exactly what a caller queried for this same partition
    and range — this function owns all quality judgment but trusts nothing
    about their internal validity, raising immediately on any structural,
    partition, range, ordering, or uniqueness violation.
    """
    validated_exchange = _validate_str_field(exchange, "exchange")
    validated_market_type = _validate_str_field(market_type, "market_type")
    validated_symbol = _validate_str_field(symbol, "symbol")

    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)
    if requested_start_us >= requested_end_us:
        raise ValueError(
            "requested_start must be strictly before requested_end, got "
            f"requested_start={requested_start!r}, requested_end={requested_end!r}"
        )

    _validate_events(
        events,
        exchange=validated_exchange,
        market_type=validated_market_type,
        symbol=validated_symbol,
        requested_start=requested_start,
        requested_end=requested_end,
        requested_start_us=requested_start_us,
        requested_end_us=requested_end_us,
    )

    gaps = _compute_coverage_gaps(
        coverage_intervals,
        requested_start=requested_start,
        requested_end=requested_end,
        requested_start_us=requested_start_us,
        requested_end_us=requested_end_us,
    )

    coverage_gap_count = len(gaps)
    overall_status = "PASS" if coverage_gap_count == 0 else "FAIL"

    return FundingDataQualityReport(
        exchange=validated_exchange,
        market_type=validated_market_type,
        symbol=validated_symbol,
        requested_start=requested_start,
        requested_end=requested_end,
        event_count=len(events),
        coverage_gap_count=coverage_gap_count,
        coverage_gaps=tuple(gaps[:_MAX_SAMPLES]),
        overall_status=overall_status,
    )


def build_funding_data_quality_report_from_store(
    store: HistoricalFundingStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    requested_start: datetime,
    requested_end: datetime,
) -> FundingDataQualityReport:
    """Query `store` over `[requested_start, requested_end)` and build its quality report.

    Read-only: only `query_events`/`query_coverage` are called, never a
    write path. All quality judgment is delegated entirely to
    `build_funding_data_quality_report` — no logic is duplicated here.
    """
    validated_exchange = _validate_str_field(exchange, "exchange")
    validated_market_type = _validate_str_field(market_type, "market_type")
    validated_symbol = _validate_str_field(symbol, "symbol")

    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)
    if requested_start_us >= requested_end_us:
        raise ValueError(
            "requested_start must be strictly before requested_end, got "
            f"requested_start={requested_start!r}, requested_end={requested_end!r}"
        )

    events = store.query_events(
        exchange=validated_exchange,
        market_type=validated_market_type,
        symbol=validated_symbol,
        start_time=requested_start,
        end_time=requested_end,
    )
    coverage_intervals = store.query_coverage(
        exchange=validated_exchange,
        market_type=validated_market_type,
        symbol=validated_symbol,
        start_time=requested_start,
        end_time=requested_end,
    )

    return build_funding_data_quality_report(
        exchange=validated_exchange,
        market_type=validated_market_type,
        symbol=validated_symbol,
        requested_start=requested_start,
        requested_end=requested_end,
        events=events,
        coverage_intervals=coverage_intervals,
    )
