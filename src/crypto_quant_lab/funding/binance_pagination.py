"""Binance USDⓈ-M Futures funding-rate history pagination + bounded retry.

(FUNDING_DATA_SPEC.md Bölüm 15-16, Microstep 9: "funding pagination + retry
composition")

Orchestrates `funding.binance.fetch_binance_funding_rate_page` across an
inclusive `[start_time_ms, end_time_ms]` Binance transport range into a
single, tie-safe, deduplicated `list[BinanceHistoricalFundingRecord]`.
Reuses `data_quality.retry.with_connection_retry` unmodified, composed
around each individual page fetch (not the whole pagination operation).

Funding pagination deliberately does NOT reuse
`data_quality.pagination.paginate_historical_klines` — that cursor
advances by a fixed candle duration and requires strictly-ascending
timestamps, neither of which holds for funding: the source endpoint's
`fundingTime` is only non-decreasing (`Regular`/`Special` may legitimately
share one timestamp), and safe cursor advance requires an *inclusive*
restart at the last-seen timestamp, never `+1ms` (FUNDING_DATA_SPEC.md
Bölüm 16.3-16.5) — a `+1ms` skip could silently drop an unseen sibling
charge at the same instant.

No canonical (`FundingEvent`/`HistoricalFundingEvent`) mapping, no
`mark_price -> reference_price` rename, no canonical half-open filtering,
no `source_as_of` guard, and no storage/DB access happen here — all
deferred to Microstep 10.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from crypto_quant_lab.data_quality.retry import with_connection_retry
from crypto_quant_lab.funding.binance import (
    BinanceHistoricalFundingRecord,
    fetch_binance_funding_rate_page,
)

_PAGE_LIMIT = 1000

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND
_MICROSECONDS_PER_MILLISECOND = 1_000

FetchFundingPage = Callable[..., list[BinanceHistoricalFundingRecord]]

_SourceKey = tuple[int, str]
_PageEntry = tuple[_SourceKey, BinanceHistoricalFundingRecord]


def _validate_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"symbol must be a non-empty str, got {symbol!r}")
    return symbol


def _validate_time_bound(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")  # noqa: TRY004
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _funding_time_to_ms(funding_time: object) -> int:
    """Losslessly recover the exact source `fundingTime` milliseconds.

    Local, stdlib-only, integer-only arithmetic — deliberately avoids
    `storage.sqlite_codec` to keep this source-pagination layer free of any
    storage-layer dependency (mirrors the stdlib-only boundary already kept
    by `funding/binance.py`).
    """
    if not isinstance(funding_time, datetime):
        raise ValueError(f"funding_time must be a datetime, got {type(funding_time).__name__}")  # noqa: TRY004
    if funding_time.tzinfo is None or funding_time.utcoffset() is None:
        raise ValueError(f"funding_time must be timezone-aware, got {funding_time!r}")

    delta = funding_time - _EPOCH
    total_us = (
        delta.days * _MICROSECONDS_PER_DAY
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )
    if total_us < 0:
        raise ValueError(f"funding_time must not precede the UTC epoch, got {funding_time!r}")

    quotient, remainder = divmod(total_us, _MICROSECONDS_PER_MILLISECOND)
    if remainder != 0:
        raise ValueError(f"funding_time must be exactly millisecond-aligned, got {funding_time!r}")
    return quotient


def _same_payload(a: BinanceHistoricalFundingRecord, b: BinanceHistoricalFundingRecord) -> bool:
    """Numeric Decimal equality — `Decimal("0.001") == Decimal("0.0010")` is True."""
    return a.funding_rate == b.funding_rate and a.mark_price == b.mark_price


def _validate_page(
    page: list, *, symbol: str, lower_bound_ms: int, upper_bound_ms: int
) -> list[_PageEntry]:
    """Validate one page in isolation: type, symbol, transport range, order, same-page duplicates.

    `lower_bound_ms`/`upper_bound_ms` are the exact Binance inclusive
    transport bounds sent on this request (`current cursor`, original
    `end_time_ms`) — not canonical `[start, end)` filtering (Microstep 10).
    A record exactly at either bound is legal; a record outside it is a
    source/transport contract violation.

    Returns `(source_key, record)` pairs in page order. Raises `ValueError`
    on the first violation — nothing here mutates any paginator state.
    """
    entries: list[_PageEntry] = []
    page_keys: set[_SourceKey] = set()
    previous_ms: int | None = None

    for record in page:
        if not isinstance(record, BinanceHistoricalFundingRecord):
            raise ValueError(  # noqa: TRY004
                f"page record must be a BinanceHistoricalFundingRecord, got {type(record).__name__}"
            )
        if record.symbol != symbol:
            raise ValueError(
                f"page record symbol mismatch: expected {symbol!r}, got {record.symbol!r}"
            )

        record_ms = _funding_time_to_ms(record.funding_time)

        if record_ms < lower_bound_ms:
            raise ValueError(
                f"record timestamp {record_ms} is behind requested cursor {lower_bound_ms}"
            )
        if record_ms > upper_bound_ms:
            raise ValueError(
                f"record timestamp {record_ms} exceeds requested end_time_ms {upper_bound_ms}"
            )

        if previous_ms is not None and record_ms < previous_ms:
            raise ValueError(
                f"page timestamps are not non-decreasing: {record_ms} follows {previous_ms}"
            )
        previous_ms = record_ms

        key: _SourceKey = (record_ms, record.rate_type)
        if key in page_keys:
            raise ValueError(f"duplicate source key within one page: {key!r}")
        page_keys.add(key)

        entries.append((key, record))

    return entries


def fetch_binance_funding_history(
    symbol: str,
    start_time_ms: int,
    end_time_ms: int,
    *,
    timeout: float = 10.0,
    max_attempts: int = 3,
    fetch_page: FetchFundingPage | None = None,
) -> list[BinanceHistoricalFundingRecord]:
    """Paginate Binance USDⓈ-M funding-rate history over inclusive `[start_time_ms, end_time_ms]`.

    Tie-safe: cursor restarts are always inclusive at the last-seen
    timestamp, never `+1ms` — a same-timestamp `Regular`/`Special` sibling
    unseen in a prior page is never silently skipped. Each page fetch is
    independently wrapped in `with_connection_retry`; `ValueError` from
    parsing, ordering, duplicate-key, or no-progress validation is never
    retried and propagates immediately. `fetch_page`, when supplied,
    replaces the production single-page client for offline tests.
    """
    validated_symbol = _validate_symbol(symbol)
    validated_start = _validate_time_bound("start_time_ms", start_time_ms)
    validated_end = _validate_time_bound("end_time_ms", end_time_ms)
    if validated_start > validated_end:
        raise ValueError(
            f"start_time_ms ({validated_start}) must be <= end_time_ms ({validated_end})"
        )

    raw_fetch_page = fetch_page if fetch_page is not None else fetch_binance_funding_rate_page
    retrying_fetch_page = with_connection_retry(raw_fetch_page, max_attempts=max_attempts)

    seen: dict[_SourceKey, BinanceHistoricalFundingRecord] = {}
    output: list[BinanceHistoricalFundingRecord] = []
    cursor_ms = validated_start

    while True:
        page = retrying_fetch_page(
            validated_symbol,
            cursor_ms,
            validated_end,
            limit=_PAGE_LIMIT,
            timeout=timeout,
        )

        if len(page) > _PAGE_LIMIT:
            raise ValueError(f"page exceeded _PAGE_LIMIT: got {len(page)}, limit is {_PAGE_LIMIT}")

        page_entries = _validate_page(
            page, symbol=validated_symbol, lower_bound_ms=cursor_ms, upper_bound_ms=validated_end
        )

        unseen_count = 0
        for key, record in page_entries:
            existing = seen.get(key)
            if existing is None:
                unseen_count += 1
            elif not _same_payload(existing, record):
                raise ValueError(f"conflicting payload for source key {key!r}: {record!r}")

        is_full_page = len(page) == _PAGE_LIMIT
        if is_full_page and unseen_count == 0:
            raise ValueError(
                "full page produced no unseen source keys — cannot safely advance cursor "
                f"without risking silent data loss (cursor_ms={cursor_ms})"
            )

        for key, record in page_entries:
            if key not in seen:
                seen[key] = record
                output.append(record)

        if not is_full_page:
            return output

        cursor_ms = page_entries[-1][0][0]
