"""Pure, store-free temporal window / IS-OOS split primitives (VALIDATION_SPEC.md Bölüm 6, 7).

Passive, immutable value objects only — no store access, no backtest engine,
no policy/cost/funding knowledge, no data query. A generic OOS runner is out
of scope for this microstep (VALIDATION_SPEC.md Bölüm 22/23).
"""

from dataclasses import dataclass
from datetime import datetime

from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


@dataclass(frozen=True, slots=True)
class TemporalWindow:
    """A validated, half-open `[start, end)` temporal window (VALIDATION_SPEC.md Bölüm 6).

    Timeframe-agnostic by design — a generic temporal window may conceptually
    exist independently of any backtest usage. Grid alignment is enforced by
    `TemporalSplit`, the backtest-usable object that actually carries a
    timeframe (Bölüm 7: "her iki aralık da grid-alignment kuralına tabidir").
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        datetime_to_epoch_us(self.start)  # validates genuine, aware, non-pseudo-naive
        datetime_to_epoch_us(self.end)

        if self.start >= self.end:
            raise ValueError(
                f"start must be strictly before end, got start={self.start!r}, end={self.end!r}"
            )


def _require_temporal_window(value: object, field_name: str) -> None:
    if not isinstance(value, TemporalWindow):
        raise TypeError(f"{field_name} must be a TemporalWindow, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    """A validated, non-overlapping in-sample/out-of-sample split (VALIDATION_SPEC.md Bölüm 7).

    `in_sample.end <= out_of_sample.start` is the single invariant that
    forbids overlap, allows adjacency and a gap, and rejects reversed IS/OOS
    chronology — with no boundary mutation. A gap between the two windows
    carries no formal embargo semantics; it is not computed, labeled, or
    enforced here (Bölüm 7, 17.1).
    """

    in_sample: TemporalWindow
    out_of_sample: TemporalWindow
    timeframe: str

    def __post_init__(self) -> None:
        _require_temporal_window(self.in_sample, "in_sample")
        _require_temporal_window(self.out_of_sample, "out_of_sample")

        for boundary_name, value in (
            ("in_sample.start", self.in_sample.start),
            ("in_sample.end", self.in_sample.end),
            ("out_of_sample.start", self.out_of_sample.start),
            ("out_of_sample.end", self.out_of_sample.end),
        ):
            if not is_grid_aligned(value, self.timeframe):
                raise ValueError(
                    f"{boundary_name} is not aligned to the {self.timeframe!r} grid: {value!r}"
                )

        if self.in_sample.end > self.out_of_sample.start:
            raise ValueError(
                "in_sample and out_of_sample must not overlap: in_sample.end="
                f"{self.in_sample.end!r} must be <= out_of_sample.start="
                f"{self.out_of_sample.start!r}"
            )
