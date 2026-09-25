"""Offline CPCV research study with an input eligibility assessment (VALIDATION_SPEC.md Bölüm 17.2.26-17.2.33, 28.R).

One path from real evidence to a CPCV diagnostic:
`TrialGroup` -> `build_trial_return_matrix` -> fold groups (by default aligned
to the group's own rolling evaluation windows) -> `build_combinatorial_fold_model`
-> input assessment -> `compute_cpcv_paths` -> `summarize_cpcv_paths`.

The assessment keeps two different horizons apart and never estimates either
from returns:
- forward OUTCOME horizon (how long a decision's position keeps producing
  returns): not recorded anywhere (`BacktestResult` has no fill times). What
  IS recorded is the rolling backtest window of every matrix row
  (`window_indices`); every window is an independent backtest that starts
  flat, so an outcome can never cross a window boundary. A fold boundary
  inside a window is therefore "horizon unknown" -> failed, unless the
  conservative window purge (`purge_shared_backtest_windows`) is enabled;
- backward LOOKBACK (how much history a window's policy may read): recorded
  only in the caller's `ContextAwareWindow`s (`context_start`); `WindowResult`
  drops it. Without them the lookback check is `not_evaluated`, never
  assumed to be zero.

A study runs CPCV only when no check failed; `not_evaluated` checks are
reported and make the study "not fully evaluated". Offline research
diagnostic only — not a full CPCV validation, not a live strategy approval.
"""

from dataclasses import dataclass as _dataclass
from datetime import timedelta as _timedelta
from decimal import Decimal as _Decimal

from crypto_quant_lab.validation.combinatorial_folds import (
    CombinatorialFoldModel as _CombinatorialFoldModel,
)
from crypto_quant_lab.validation.combinatorial_folds import (
    build_combinatorial_fold_model as _build_combinatorial_fold_model,
)
from crypto_quant_lab.validation.cpcv import CpcvPathSummary as _CpcvPathSummary
from crypto_quant_lab.validation.cpcv import CpcvResult as _CpcvResult
from crypto_quant_lab.validation.cpcv import compute_cpcv_paths as _compute_cpcv_paths
from crypto_quant_lab.validation.cpcv import summarize_cpcv_paths as _summarize_cpcv_paths
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix as _TrialReturnMatrix
from crypto_quant_lab.validation.return_matrix import (
    build_trial_return_matrix as _build_trial_return_matrix,
)
from crypto_quant_lab.validation.rolling import ContextAwareWindow as _ContextAwareWindow
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow as _TemporalWindow

CHECK_STATUSES = ("passed", "failed", "not_evaluated")
STUDY_SCOPE = (
    "offline research diagnostic: CPCV over recorded rolling-backtest evidence; not a full CPCV "
    "validation and not a live strategy approval; no observation-level (fill-time) outcome "
    "purging exists — cross-fold outcome overlap is excluded only by window alignment or the "
    "conservative shared-window purge"
)


@_dataclass(frozen=True, slots=True)
class CpcvInputCheck:
    name: str
    status: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in CHECK_STATUSES:
            raise ValueError(f"status must be one of {CHECK_STATUSES}, got {self.status!r}")


@_dataclass(frozen=True, slots=True)
class CpcvInputAssessment:
    checks: tuple[CpcvInputCheck, ...]

    @property
    def runnable(self) -> bool:
        """No check failed (`not_evaluated` checks do not block)."""
        return all(check.status != "failed" for check in self.checks)

    @property
    def fully_evaluated(self) -> bool:
        return all(check.status == "passed" for check in self.checks)

    def status(self, name: str) -> str:
        return next(check.status for check in self.checks if check.name == name)


@_dataclass(frozen=True, slots=True)
class CpcvStudy:
    scope: str
    assessment: CpcvInputAssessment
    matrix: _TrialReturnMatrix
    fold_model: _CombinatorialFoldModel
    result: _CpcvResult | None
    summary: _CpcvPathSummary | None


def window_aligned_fold_groups(
    windows: tuple[_TemporalWindow, ...],
) -> tuple[_TemporalWindow, ...]:
    """One contiguous fold group per evaluation window: [w_i.start, w_{i+1}.start), last [start, end).

    Rows of window i (owned by start < t <= end) fall exactly in group i, so no
    fold boundary lies inside a backtest window; gaps between windows are
    absorbed by the preceding group and hold no rows.
    """
    if not isinstance(windows, tuple):
        raise TypeError(f"windows must be a tuple, got {type(windows).__name__}")
    for index, window in enumerate(windows):
        if not isinstance(window, _TemporalWindow):
            raise TypeError(
                f"windows[{index}] must be a TemporalWindow, got {type(window).__name__}"
            )
    if len(windows) < 2:
        raise ValueError(f"at least 2 evaluation windows are required, got {len(windows)}")
    for index in range(1, len(windows)):
        if windows[index].start < windows[index - 1].end:
            raise ValueError(
                f"windows[{index}] must start at or after windows[{index - 1}].end "
                "(chronological, non-overlapping)"
            )
    groups = [
        _TemporalWindow(start=windows[i].start, end=windows[i + 1].start)
        for i in range(len(windows) - 1)
    ]
    groups.append(windows[-1])
    return tuple(groups)


def _block_rows(matrix: _TrialReturnMatrix) -> list[list[int]]:
    blocks: list[list[int]] = [[] for _ in range(matrix.window_indices[-1] + 1)]
    for row, block in enumerate(matrix.window_indices):
        blocks[block].append(row)
    return blocks


def _owner(fold_model: _CombinatorialFoldModel, time) -> int | None:
    return next((g for g, w in enumerate(fold_model.groups) if w.start < time <= w.end), None)


def assess_cpcv_inputs(
    matrix: _TrialReturnMatrix,
    fold_model: _CombinatorialFoldModel,
    *,
    evaluation_windows: tuple[_TemporalWindow, ...],
    lookback_windows: tuple[_ContextAwareWindow, ...] | None = None,
    purge_shared_backtest_windows: bool = False,
    position_intervals_supplied: bool = False,
) -> CpcvInputAssessment:
    """Eligibility of (matrix, fold model) for CPCV (Bölüm 17.2.27-17.2.30)."""
    if not isinstance(matrix, _TrialReturnMatrix):
        raise TypeError(f"matrix must be a TrialReturnMatrix, got {type(matrix).__name__}")
    if not isinstance(fold_model, _CombinatorialFoldModel):
        raise TypeError(
            f"fold_model must be a CombinatorialFoldModel, got {type(fold_model).__name__}"
        )
    if not isinstance(evaluation_windows, tuple) or not all(
        isinstance(w, _TemporalWindow) for w in evaluation_windows
    ):
        raise TypeError("evaluation_windows must be a tuple of TemporalWindow")
    if lookback_windows is not None and (
        not isinstance(lookback_windows, tuple)
        or not all(isinstance(w, _ContextAwareWindow) for w in lookback_windows)
    ):
        raise TypeError("lookback_windows must be None or a tuple of ContextAwareWindow")
    if not isinstance(purge_shared_backtest_windows, bool):
        raise TypeError("purge_shared_backtest_windows must be a bool")

    names = ("provenance.evaluation_windows", "rows.fold_groups", "outcome_horizon.backtest_windows",
             "lookback.context")  # fmt: skip
    checks: list[CpcvInputCheck] = []

    def skip_rest(reason: str) -> CpcvInputAssessment:
        for name in names[len(checks) :]:
            checks.append(CpcvInputCheck(name, "not_evaluated", reason))
        return CpcvInputAssessment(tuple(checks))

    # 1. the matrix rows really come from the declared evaluation windows
    blocks = _block_rows(matrix)
    times = matrix.observation_times
    problem = None
    if len(blocks) != len(evaluation_windows):
        problem = (
            f"matrix has {len(blocks)} window block(s), evaluation_windows has "
            f"{len(evaluation_windows)}"
        )
    else:
        for block, rows in enumerate(blocks):
            window = evaluation_windows[block]
            bad = next((r for r in rows if not window.start < times[r] <= window.end), None)
            if bad is not None:
                problem = (
                    f"row {bad} (window block {block}) at {times[bad].isoformat()} is not inside "
                    f"evaluation window {block}"
                )
                break
    if problem:
        checks.append(CpcvInputCheck(names[0], "failed", problem))
        return skip_rest("the evaluation-window provenance check failed")
    checks.append(
        CpcvInputCheck(
            names[0],
            "passed",
            f"{len(blocks)} window block(s) match the evaluation windows row by row",
        )
    )

    # 2. every row in exactly one fold group, every group owns rows
    owners = [_owner(fold_model, t) for t in times]
    if None in owners:
        row = owners.index(None)
        checks.append(
            CpcvInputCheck(
                names[1], "failed", f"row {row} at {times[row].isoformat()} is in no fold group"
            )
        )
        return skip_rest("the row/fold-group ownership check failed")
    empty = [g for g in range(len(fold_model.groups)) if g not in owners]
    if empty:
        checks.append(CpcvInputCheck(names[1], "failed", f"fold group(s) {empty} own no row"))
        return skip_rest("the row/fold-group ownership check failed")
    checks.append(
        CpcvInputCheck(names[1], "passed", f"{len(owners)} rows in {len(fold_model.groups)} groups")
    )

    # 3. forward outcome horizon: can a position carry across a fold boundary?
    split_windows = [b for b, rows in enumerate(blocks) if len({owners[r] for r in rows}) > 1]
    if not split_windows:
        checks.append(
            CpcvInputCheck(
                names[2],
                "passed",
                "no fold boundary falls inside a rolling backtest window; every window is an "
                "independent backtest starting flat, so no outcome can cross a fold boundary",
            )
        )
    elif position_intervals_supplied:
        checks.append(
            CpcvInputCheck(
                names[2],
                "passed",
                f"fold boundaries fall inside backtest window(s) {split_windows}; recorded "
                "position intervals drive observation-level purging: a training row is dropped "
                "when a position producing its return also produces a return in a test group",
            )
        )
    elif purge_shared_backtest_windows:
        checks.append(
            CpcvInputCheck(
                names[2],
                "passed",
                f"fold boundaries fall inside backtest window(s) {split_windows}; the "
                "shared-window purge drops every training row that shares a backtest window with "
                "a test row (conservative: the unrecorded holding horizon ends at the window end)",
            )
        )
    else:
        checks.append(
            CpcvInputCheck(
                names[2],
                "failed",
                f"fold boundaries fall inside backtest window(s) {split_windows}: a position may "
                "carry across the boundary for an unrecorded time (BacktestResult has no fill "
                "times); align the fold groups to the windows or enable "
                "purge_shared_backtest_windows",
            )
        )

    # 4. backward lookback: can a training window read a test group's data?
    if lookback_windows is None:
        checks.append(
            CpcvInputCheck(
                names[3],
                "not_evaluated",
                "lookback not supplied: WindowResult/TrialGroup do not record context_start; "
                "pass the ContextAwareWindows used for the rolling run (context_start == "
                "evaluation.start declares zero context)",
            )
        )
        return CpcvInputAssessment(tuple(checks))
    if tuple(w.evaluation for w in lookback_windows) != evaluation_windows:
        checks.append(
            CpcvInputCheck(
                names[3], "failed", "lookback windows do not describe the evaluated windows"
            )
        )
        return CpcvInputAssessment(tuple(checks))
    lookback = max(w.evaluation.start - w.context_start for w in lookback_windows)
    groups = fold_model.groups
    violation = None
    for split in fold_model.splits:
        train, test = set(split.train_groups), split.test_groups
        test_blocks = {matrix.window_indices[r] for r, g in enumerate(owners) if g in test}
        for block, rows in enumerate(blocks):
            train_rows = [r for r in rows if owners[r] in train]
            if purge_shared_backtest_windows and block in test_blocks:
                train_rows = []
            if not train_rows:
                continue
            window = lookback_windows[block]
            start, end = window.context_start, window.evaluation.start
            hit = next((t for t in test if start < end and start < groups[t].end
                        and groups[t].start < end), None)  # fmt: skip
            if hit is not None:
                violation = (split.split_index, block, hit, end - start)
                break
        if violation:
            break
    if violation:
        split_index, block, test_group, span = violation
        checks.append(
            CpcvInputCheck(
                names[3],
                "failed",
                f"split {split_index}: training window {block} reads {span} of context that "
                f"overlaps test group {test_group}; embargo {fold_model.embargo} does not cover "
                f"the lookback (max {lookback})",
            )
        )
    else:
        checks.append(
            CpcvInputCheck(
                names[3],
                "passed",
                f"max lookback {lookback}; no training window's context overlaps a test group "
                "in any split",
            )
        )
    return CpcvInputAssessment(tuple(checks))


def _interval_provenance(group: _TrialGroup, position_intervals: object) -> CpcvInputCheck:
    """Recorded intervals must reproduce every trial's recorded trade_count, window by window."""
    name = "outcome_horizon.position_intervals"
    ids = [trial.candidate.candidate_id for trial in group.trials]
    if not isinstance(position_intervals, dict) or set(position_intervals) != set(ids):
        return CpcvInputCheck(
            name, "failed", "position intervals must be keyed by every candidate id"
        )
    for trial in group.trials:
        per_window = position_intervals[trial.candidate.candidate_id]
        if not isinstance(per_window, tuple) or len(per_window) != len(trial.results):
            return CpcvInputCheck(
                name, "failed",
                f"{trial.candidate.candidate_id}: one interval tuple per window is required",
            )  # fmt: skip
        for index, (window_result, intervals) in enumerate(
            zip(trial.results, per_window, strict=True)
        ):
            transitions = sum(1 if i.exit_time is None else 2 for i in intervals)
            if transitions != window_result.result.trade_count:
                return CpcvInputCheck(
                    name,
                    "failed",
                    f"{trial.candidate.candidate_id} window {index}: intervals imply {transitions} "
                    f"trade transitions, the backtest recorded {window_result.result.trade_count}",
                )
    return CpcvInputCheck(
        name,
        "passed",
        "recorded position intervals reproduce every trial's trade_count in every window",
    )


def run_cpcv_study(
    group: _TrialGroup,
    *,
    test_group_count: int,
    embargo: _timedelta = _timedelta(0),
    lookback_windows: tuple[_ContextAwareWindow, ...] | None = None,
    fold_groups: tuple[_TemporalWindow, ...] | None = None,
    risk_free_per_period: _Decimal = _Decimal(0),
    purge_shared_backtest_windows: bool = False,
    position_intervals: dict | None = None,
) -> CpcvStudy:
    """TrialGroup -> matrix -> fold model -> assessment -> CPCV paths -> summary (Bölüm 17.2.31)."""
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
    matrix = _build_trial_return_matrix(group)
    evaluation_windows = tuple(result.window for result in group.trials[0].results)
    groups = window_aligned_fold_groups(evaluation_windows) if fold_groups is None else fold_groups
    fold_model = _build_combinatorial_fold_model(
        groups, test_group_count=test_group_count, embargo=embargo
    )
    assessment = assess_cpcv_inputs(
        matrix,
        fold_model,
        evaluation_windows=evaluation_windows,
        lookback_windows=lookback_windows,
        purge_shared_backtest_windows=purge_shared_backtest_windows,
        position_intervals_supplied=position_intervals is not None,
    )
    if position_intervals is not None:
        assessment = CpcvInputAssessment(
            assessment.checks + (_interval_provenance(group, position_intervals),)
        )
    result = summary = None
    if assessment.runnable:
        try:
            result = _compute_cpcv_paths(
                matrix,
                fold_model,
                risk_free_per_period=risk_free_per_period,
                purge_shared_backtest_windows=purge_shared_backtest_windows,
                position_intervals=position_intervals,
            )
            if len(result.paths) >= 2:
                summary = _summarize_cpcv_paths(result)
        except ValueError as exc:  # undefined Sharpe / too few training rows: reported
            result = None
            assessment = CpcvInputAssessment(
                assessment.checks + (CpcvInputCheck("cpcv.computation", "failed", str(exc)),)
            )
    return CpcvStudy(
        scope=STUDY_SCOPE,
        assessment=assessment,
        matrix=matrix,
        fold_model=fold_model,
        result=result,
        summary=summary,
    )
