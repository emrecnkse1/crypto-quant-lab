"""Additive position-interval provenance for the single-leg replay (BACKTEST_SPEC.md Bölüm 36).

`run_backtest_replay(..., position_observer=recorder)` calls
`recorder.on_fill(fill_time=..., old_quantity=..., new_quantity=...)` after
every fill that changes the position. The observer is read-only: it never
touches `AccountState`, costs or the returned `BacktestResult`, which stays
byte-for-byte what it is without an observer.

Timing (Faz 4 contract, unchanged): the decision taken at candle N's
availability fills at candle N+1's OPEN, stamped `open_time(N+1) ==
availability(N)`. A position entered at time e and exited at time x therefore
produces the returns of every equity mark t with e < t <= x (the mark at x is
taken before the exiting fill). A position still open at the end has
`exit_time is None`; no synthetic exit is ever recorded.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


@dataclass(frozen=True, slots=True)
class PositionInterval:
    side: str  # "LONG" or "SHORT"
    quantity: Decimal  # absolute
    entry_time: datetime
    exit_time: datetime | None

    def __post_init__(self) -> None:
        if self.side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be 'LONG' or 'SHORT', got {self.side!r}")
        if not isinstance(self.quantity, Decimal) or not self.quantity > 0:
            raise ValueError(f"quantity must be a positive Decimal, got {self.quantity!r}")
        datetime_to_epoch_us(self.entry_time)
        if self.exit_time is not None and not self.exit_time > self.entry_time:
            raise ValueError("exit_time must be after entry_time")

    def produces_return_at(self, mark_time: datetime) -> bool:
        """True when the equity mark at `mark_time` carries this position's return."""
        return self.entry_time < mark_time and (
            self.exit_time is None or mark_time <= self.exit_time
        )


class PositionIntervalRecorder:
    """Records `PositionInterval`s from replay fills; one recorder per backtest."""

    def __init__(self) -> None:
        self._closed: list[PositionInterval] = []
        self._open: tuple[str, Decimal, datetime] | None = None

    def on_fill(self, *, fill_time: datetime, old_quantity: Decimal, new_quantity: Decimal) -> None:
        if old_quantity == new_quantity:
            raise ValueError("on_fill requires a position change")
        if old_quantity != 0:
            if self._open is None:
                raise ValueError("a fill closed a position the recorder never saw opened")
            side, quantity, entry = self._open
            self._closed.append(PositionInterval(side, quantity, entry, fill_time))
            self._open = None
        elif self._open is not None:
            raise ValueError("a fill from flat arrived while a position was recorded open")
        if new_quantity != 0:
            self._open = ("LONG" if new_quantity > 0 else "SHORT", abs(new_quantity), fill_time)

    @property
    def intervals(self) -> tuple[PositionInterval, ...]:
        still_open = ()
        if self._open is not None:
            side, quantity, entry = self._open
            still_open = (PositionInterval(side, quantity, entry, None),)
        return tuple(self._closed) + still_open

    @property
    def transition_count(self) -> int:
        """Trade-transition entries implied by the intervals (BACKTEST_SPEC Bölüm 19 count)."""
        return sum(1 if interval.exit_time is None else 2 for interval in self.intervals)
