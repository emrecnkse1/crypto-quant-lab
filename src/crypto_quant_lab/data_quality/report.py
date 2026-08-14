"""Immutable, passive quality report domain model (DATA_QUALITY_SPEC.md Bölüm 13).

Pure data container — no computation. It does not walk the expected grid,
compute missing/unaligned sets, decide `overall_status`, compute the
incomplete tail, normalize/convert datetimes, or read the wall clock. Every
field is stored exactly as given by the caller; correctness of the values
themselves (grid-walk, set-difference, PASS/FAIL decision) is Microstep 11's
responsibility, and wiring against a real store is Microstep 12's.
"""

from dataclasses import dataclass
from datetime import datetime

_ALLOWED_STATUSES = frozenset({"PASS", "FAIL"})
_MAX_SAMPLES = 20

_NON_NEGATIVE_COUNT_FIELDS = (
    "expected_count",
    "actual_count",
    "missing_count",
    "unaligned_count",
    "incomplete_tail_excluded_count",
)

_SAMPLE_FIELDS = ("missing_samples", "unaligned_samples")


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    exchange: str
    market_type: str
    symbol: str
    timeframe: str

    requested_start: datetime
    requested_end: datetime
    as_of_time: datetime
    effective_end: datetime

    first_observed_open_time: datetime | None
    last_observed_open_time: datetime | None

    expected_count: int
    actual_count: int

    missing_count: int
    missing_samples: tuple[datetime, ...]

    unaligned_count: int
    unaligned_samples: tuple[datetime, ...]

    incomplete_tail_excluded_count: int

    overall_status: str

    def __post_init__(self) -> None:
        if self.overall_status not in _ALLOWED_STATUSES:
            raise ValueError(
                f"overall_status must be one of {sorted(_ALLOWED_STATUSES)}, "
                f"got {self.overall_status!r}"
            )

        for field_name in _NON_NEGATIVE_COUNT_FIELDS:
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value}")

        for field_name in _SAMPLE_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
            if len(value) > _MAX_SAMPLES:
                raise ValueError(
                    f"{field_name} must contain at most {_MAX_SAMPLES} samples, got {len(value)}"
                )
