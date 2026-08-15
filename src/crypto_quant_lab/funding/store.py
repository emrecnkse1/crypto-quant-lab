"""Backend-neutral historical funding storage protocol (FUNDING_DATA_SPEC.md Bölüm 17-30).

`HistoricalFundingStore` is a minimal, structural (`typing.Protocol`)
abstraction over three funding-domain capabilities only — atomic
events+coverage ingestion, event range query, coverage range query — plus
backend lifecycle (`close`). It carries no SQLite/backend knowledge, no
Binance/source knowledge, no `source_as_of`/settled-guard knowledge (that
belongs to the ingestion layer, before events ever reach this store), and no
coverage-completeness/quality reasoning (that belongs to a future pure
quality layer, which computes union/completeness from the raw intervals
`query_coverage` returns). This module contains no runtime storage
implementation — behavioral contracts are documented here for a concrete
SQLite implementation (a later microstep) to satisfy.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from crypto_quant_lab.funding.models import FundingCoverageInterval, HistoricalFundingEvent


class HistoricalFundingStore(Protocol):
    """Backend-neutral contract for a historical funding store."""

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
        """Atomically persist `events` together with one coverage statement.

        `events` may be empty — a successful, zero-event authoritative
        source retrieval is a legitimate, PASS-eligible outcome
        (FUNDING_DATA_SPEC.md Bölüm 13, 44), and coverage must still be
        recorded even then.

        `exchange`/`market_type`/`symbol` are matched exactly, never
        normalized. Every element of `events` must belong to this explicit
        partition (`event.exchange == exchange` etc.) — a mismatch is
        rejected before any durable write. Every event's `event_time` must
        satisfy `covered_start <= event_time < covered_end` — an event
        outside the declared coverage interval is rejected before any
        durable write, even though the ingestion layer is expected to have
        already filtered globally. `covered_start` must be strictly before
        `covered_end`.

        The entire operation — every event plus the one coverage interval —
        commits atomically: all of it is persisted, or none of it is.

        Idempotency: the same canonical event key
        (`HistoricalFundingEvent.canonical_key`) with the same payload
        (`funding_rate`/`reference_price`) is a no-op, whether repeated
        within one call's `events` or across separate calls. The same
        canonical key with a *differing* payload raises
        `DataConflictError` and rejects the whole batch — whether the
        conflict is found within one call's `events` or against
        already-persisted data. The exact same `(covered_start,
        covered_end)` interval is idempotent; overlapping or adjacent but
        distinct coverage intervals are legal and both persist (no eager
        merging).

        There is no standalone event-only or coverage-only persistence
        path — this is the only public write method, by design, so a
        caller cannot bypass the atomic events+coverage contract.
        """
        ...

    def query_events(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistoricalFundingEvent]:
        """Return events for the exact partition over `[start_time, end_time)`.

        `exchange`/`market_type`/`symbol` are matched exactly, never
        normalized. The range is half-open: `start_time` is inclusive,
        `end_time` is exclusive; `start_time` must be strictly before
        `end_time`. Both bounds must be genuine, timezone-aware datetimes.

        Results are ordered deterministically: `event_time` ascending, then
        `rate_type` ascending. All valid same-`event_time` records with
        different `rate_type` remain independent elements — never merged,
        summed, or otherwise collapsed. No stored event is silently
        repaired, interpolated, or synthesized. No network access. An empty
        result (`[]`) is legal and is not itself a PASS/FAIL judgment.
        """
        ...

    def query_coverage(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingCoverageInterval]:
        """Return stored coverage intervals overlapping `[start_time, end_time)`.

        `exchange`/`market_type`/`symbol` are matched exactly, never
        normalized. Overlap is the half-open test `coverage.end_time >
        start_time AND coverage.start_time < end_time` — a stored interval
        need not be fully contained in the requested range to be returned.

        Returned intervals are the original, unmodified stored rows: this
        method never clips, merges, unions, or otherwise computes
        completeness — that is a future pure quality layer's
        responsibility, operating on these raw intervals. Results are
        ordered deterministically: `start_time` ascending, then `end_time`
        ascending. An empty result (`[]`) is legal.
        """
        ...

    def close(self) -> None:
        """Release any resources held by the store."""
        ...
