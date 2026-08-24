"""Window-level purging/embargo foundation (VALIDATION_SPEC.md Bölüm 17.1, 28.I).

Three independent, pure functions generalizing `TemporalSplit`'s already-locked
single-IS/single-OOS non-overlap invariant (Bölüm 7) to an arbitrary number of
in-sample windows ("purge") plus an explicit, caller-supplied post-OOS buffer
("embargo") — with no per-observation label/outcome horizon concept, which
this repository does not define (Bölüm 17.1.1, 17.1.13). Label/outcome-horizon
purging, CPCV, and any store/rolling/candidate orchestration remain out of
scope here (Bölüm 17.1.2).

No new dataclass/value object is introduced (Bölüm 17.1.3): `windows_overlap`
returns a bare `bool`, `embargo_boundary` a bare `datetime`, and
`purge_in_sample_windows` a bare `tuple[TemporalWindow, ...]`. This module
imports only `TemporalWindow` from `windows.py` and the stdlib — it never
imports `rolling`, `metrics`, `candidate`, or `annualized_metrics`, and no
existing module imports this one (Bölüm 17.1.9).
"""

from datetime import datetime, timedelta

from crypto_quant_lab.validation.windows import TemporalWindow


def _require_temporal_window(value: object, field_name: str) -> None:
    if not isinstance(value, TemporalWindow):
        raise TypeError(f"{field_name} must be a TemporalWindow, got {type(value).__name__}")


def _require_timedelta(value: object, field_name: str) -> None:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta, got {type(value).__name__}")


def _require_non_negative_embargo(embargo: timedelta) -> None:
    if embargo < timedelta(0):
        raise ValueError(f"embargo must be >= timedelta(0), got {embargo!r}")


def windows_overlap(first: TemporalWindow, second: TemporalWindow) -> bool:
    """Half-open `[start, end)` overlap predicate (VALIDATION_SPEC.md Bölüm 17.1.5).

    Touching windows (`a.end == b.start`) do not overlap. Symmetric:
    `windows_overlap(a, b) == windows_overlap(b, a)`. `first`/`second` are
    trusted to already carry a genuine-aware-datetime, `start < end`
    invariant from their own `TemporalWindow.__post_init__` — this function
    does not re-validate datetime awareness.
    """
    _require_temporal_window(first, "first")
    _require_temporal_window(second, "second")

    return first.start < second.end and second.start < first.end


def embargo_boundary(out_of_sample: TemporalWindow, *, embargo: timedelta) -> datetime:
    """Exclusive upper bound of the post-OOS embargo zone (VALIDATION_SPEC.md Bölüm 17.1.6).

    `out_of_sample.end + embargo` — a zero embargo degenerates to exactly
    `out_of_sample.end` (empty zone, Bölüm 17.1.4). An `embargo` large enough
    to exceed `datetime.max` propagates Python's own `OverflowError`
    unchanged; no new overflow check is introduced.
    """
    _require_temporal_window(out_of_sample, "out_of_sample")
    _require_timedelta(embargo, "embargo")
    _require_non_negative_embargo(embargo)

    return out_of_sample.end + embargo


def purge_in_sample_windows(
    in_sample_windows: tuple[TemporalWindow, ...],
    *,
    out_of_sample: TemporalWindow,
    embargo: timedelta = timedelta(0),
) -> tuple[TemporalWindow, ...]:
    """Filter `in_sample_windows` rejecting OOS-overlap and embargo-zone overlap.

    (VALIDATION_SPEC.md Bölüm 17.1.7, 17.1.8.) Exact validation order: type of
    `out_of_sample`, type of `embargo`, non-negativity of `embargo`, type of
    `in_sample_windows` itself, then EVERY element's type (a global pass — no
    element is filtered until all elements pass this check). Only then does
    filtering run.

    For each `in_sample` window (original order preserved, no sort/dedupe):
    reject if it overlaps `out_of_sample` directly (purge); otherwise, only
    when `embargo > timedelta(0)`, also reject if it overlaps the
    `[out_of_sample.end, embargo_boundary)` zone (embargo). No
    `TemporalWindow` is constructed for a zero-length embargo zone.
    """
    _require_temporal_window(out_of_sample, "out_of_sample")
    _require_timedelta(embargo, "embargo")
    _require_non_negative_embargo(embargo)

    if not isinstance(in_sample_windows, tuple):
        raise TypeError(
            f"in_sample_windows must be a tuple, got {type(in_sample_windows).__name__}"
        )
    for index, candidate_window in enumerate(in_sample_windows):
        if not isinstance(candidate_window, TemporalWindow):
            raise TypeError(
                f"in_sample_windows[{index}] must be a TemporalWindow, got "
                f"{type(candidate_window).__name__}"
            )

    purged: list[TemporalWindow] = []
    for in_sample in in_sample_windows:
        reject = windows_overlap(in_sample, out_of_sample)
        if not reject and embargo > timedelta(0):
            embargo_zone = TemporalWindow(
                start=out_of_sample.end,
                end=embargo_boundary(out_of_sample, embargo=embargo),
            )
            reject = windows_overlap(in_sample, embargo_zone)
        if not reject:
            purged.append(in_sample)

    return tuple(purged)
