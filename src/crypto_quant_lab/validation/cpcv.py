"""CPCV path returns with training-set selection (VALIDATION_SPEC.md Bölüm 17.2.11-17.2.19, 28.P).

User decision §17.2.10 A, for this OFFLINE research diagnostic only: in every
split of a `CombinatorialFoldModel` the candidate(s) with the highest Stage-2
Sharpe ratio over the TRAINING rows are selected (PBO's rule, Bölüm 17.5.15.5:
every tied candidate is selected, in column order); the selection is then
read on the split's TEST rows. Path p concatenates, for each group g in time
order, the test-row returns of the candidate(s) selected in split
`path_split_indices[p][g]`; a tied selection contributes the arithmetic mean
of the tied candidates' returns (the expectation under PBO's uniform
tie-break) and is flagged. Each path's Stage-2 Sharpe ratio is reported.

Rows are matched to groups by `TrialReturnMatrix`'s own ownership rule,
`start < observation_time <= end` (Bölüm 17.5). Training rows are only the
rows of `train_groups`: test and embargoed rows never enter the selection.
Purging is window-level only (embargo after each test group); there is no
label/outcome-horizon purging (Bölüm 17.1.13).

This is not a trading strategy, a general candidate-selection policy, a
pass/fail verdict or "CPCV complete": no threshold, no ranking beyond the
per-split argmax, no orders.
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext

from crypto_quant_lab.backtest.position_log import PositionInterval as _PositionInterval
from crypto_quant_lab.validation.combinatorial_folds import (
    CombinatorialFoldModel as _CombinatorialFoldModel,
)
from crypto_quant_lab.validation.pbo import _pbo_context, _subsample_sharpe_ratio
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix as _TrialReturnMatrix

_MAX_CELL_EVALUATIONS = 20_000_000


@_dataclass(frozen=True, slots=True)
class CpcvSplitResult:
    """One split's training-set selection (`train_sharpe_ratios` in matrix column order)."""

    split_index: int
    test_groups: tuple[int, ...]
    train_groups: tuple[int, ...]
    train_row_count: int
    train_sharpe_ratios: tuple[_Decimal, ...]
    selected_candidate_ids: tuple[str, ...]
    purged_train_row_count: int = 0

    def __post_init__(self) -> None:
        for name in ("split_index", "train_row_count", "purged_train_row_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
        for name in (
            "test_groups",
            "train_groups",
            "train_sharpe_ratios",
            "selected_candidate_ids",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be a tuple, got {type(value).__name__}")
        if not self.selected_candidate_ids:
            raise ValueError("selected_candidate_ids must not be empty")


@_dataclass(frozen=True, slots=True)
class CpcvPathResult:
    """One backtest path: `returns` has one entry per matrix row, in time order."""

    path_index: int
    split_indices: tuple[int, ...]
    returns: tuple[_Decimal, ...]
    tie_averaged_groups: tuple[int, ...]
    sharpe_ratio: _Decimal

    def __post_init__(self) -> None:
        if isinstance(self.path_index, bool) or not isinstance(self.path_index, int):
            raise TypeError(f"path_index must be an int, got {type(self.path_index).__name__}")
        for name in ("split_indices", "returns", "tie_averaged_groups"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be a tuple, got {type(value).__name__}")
        if not isinstance(self.sharpe_ratio, _Decimal) or not self.sharpe_ratio.is_finite():
            raise ValueError(f"sharpe_ratio must be a finite Decimal, got {self.sharpe_ratio!r}")


@_dataclass(frozen=True, slots=True)
class CpcvResult:
    candidate_ids: tuple[str, ...]
    row_groups: tuple[int, ...]
    splits: tuple[CpcvSplitResult, ...]
    paths: tuple[CpcvPathResult, ...]

    def __post_init__(self) -> None:
        for name in ("candidate_ids", "row_groups", "splits", "paths"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be a tuple, got {type(value).__name__}")
        for index, split in enumerate(self.splits):
            if not isinstance(split, CpcvSplitResult):
                raise TypeError(f"splits[{index}] must be a CpcvSplitResult")
        for index, path in enumerate(self.paths):
            if not isinstance(path, CpcvPathResult):
                raise TypeError(f"paths[{index}] must be a CpcvPathResult")


def _row_groups(matrix: _TrialReturnMatrix, fold_model: _CombinatorialFoldModel) -> list[int]:
    groups = fold_model.groups
    owners = []
    for row, time in enumerate(matrix.observation_times):
        owner = next((g for g, w in enumerate(groups) if w.start < time <= w.end), None)
        if owner is None:
            raise ValueError(
                f"matrix row {row} at {time.isoformat()} is outside every fold group "
                "(ownership is start < time <= end); rows are never dropped"
            )
        owners.append(owner)
    for group in range(len(groups)):
        if group not in owners:
            raise ValueError(f"fold group {group} owns no matrix row")
    return owners


def _interval_rows(matrix: _TrialReturnMatrix, position_intervals: dict) -> list[tuple[int, ...]]:
    """Matrix rows whose return each recorded interval produces (Bölüm 17.2.35, 17.2.38)."""
    if not isinstance(position_intervals, dict):
        raise TypeError(
            f"position_intervals must be a dict, got {type(position_intervals).__name__}"
        )
    if set(position_intervals) != set(matrix.candidate_ids):
        raise ValueError("position_intervals must have exactly the matrix candidate ids as keys")
    block_count = matrix.window_indices[-1] + 1
    block_rows: list[list[int]] = [[] for _ in range(block_count)]
    for row, block in enumerate(matrix.window_indices):
        block_rows[block].append(row)
    interval_rows: list[tuple[int, ...]] = []
    for candidate_id in matrix.candidate_ids:
        per_block = position_intervals[candidate_id]
        if not isinstance(per_block, tuple) or len(per_block) != block_count:
            raise ValueError(
                f"position_intervals[{candidate_id!r}] must be a tuple of {block_count} "
                "per-window interval tuples"
            )
        for block, intervals in enumerate(per_block):
            rows = block_rows[block]
            marks = [matrix.observation_times[r] for r in rows]
            row_of = dict(zip(marks, rows, strict=True))
            for index, interval in enumerate(intervals):
                if not isinstance(interval, _PositionInterval):
                    raise TypeError(
                        f"position_intervals[{candidate_id!r}][{block}][{index}] must be a "
                        "PositionInterval"
                    )
                ends_ok = interval.exit_time is None or interval.exit_time in row_of
                if interval.entry_time not in row_of or not ends_ok:
                    raise ValueError(
                        f"position_intervals[{candidate_id!r}][{block}][{index}] entry/exit time "
                        f"is not an equity mark of window block {block}"
                    )
                produced = tuple(row_of[m] for m in interval.return_marks(marks))
                if produced:
                    interval_rows.append(produced)
    return interval_rows


def compute_cpcv_paths(
    matrix: _TrialReturnMatrix,
    fold_model: _CombinatorialFoldModel,
    *,
    risk_free_per_period: _Decimal = _Decimal(0),
    purge_shared_backtest_windows: bool = False,
    position_intervals: dict | None = None,
) -> CpcvResult:
    """CPCV path returns with per-split training-set selection (Bölüm 17.2.11-17.2.19).

    `purge_shared_backtest_windows=True` (Bölüm 17.2.28) additionally drops,
    in every split, each training row that shares a rolling backtest window
    (`matrix.window_indices`) with a test row: every window is an
    independent backtest that starts flat, so an outcome can never cross a
    window boundary, but inside one window a position may carry across a fold
    boundary for an unrecorded time. The default keeps the earlier behavior.

    `position_intervals` (Bölüm 17.2.35) enables observation-level purging
    from RECORDED position intervals: `{candidate_id: (intervals of window
    block 0, block 1, ...)}` as returned by the rolling "with positions"
    runners. A training row t is dropped when, for any candidate, a position
    interval that produces t's return (entry < t <= exit) also produces a
    return inside a test group of the split. Entry/exit times must be equity
    marks of their own window block (provenance check).
    """
    if not isinstance(matrix, _TrialReturnMatrix):
        raise TypeError(f"matrix must be a TrialReturnMatrix, got {type(matrix).__name__}")
    if not isinstance(fold_model, _CombinatorialFoldModel):
        raise TypeError(
            f"fold_model must be a CombinatorialFoldModel, got {type(fold_model).__name__}"
        )
    if not isinstance(risk_free_per_period, _Decimal):
        raise TypeError(
            f"risk_free_per_period must be a Decimal, got {type(risk_free_per_period).__name__}"
        )
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")
    if not isinstance(purge_shared_backtest_windows, bool):
        raise TypeError(
            "purge_shared_backtest_windows must be a bool, got "
            f"{type(purge_shared_backtest_windows).__name__}"
        )
    candidate_count = len(matrix.candidate_ids)
    if candidate_count < 2:
        raise ValueError(
            "at least two candidates are required for a training-set selection, "
            f"got {candidate_count}"
        )
    row_groups = _row_groups(matrix, fold_model)
    row_count = len(row_groups)
    interval_rows = (
        _interval_rows(matrix, position_intervals) if position_intervals is not None else None
    )
    cell_evaluations = len(fold_model.splits) * row_count * candidate_count
    if cell_evaluations > _MAX_CELL_EVALUATIONS:
        raise ValueError(
            f"CPCV cost of {cell_evaluations} cell evaluations (splits={len(fold_model.splits)} "
            f"x T={row_count} x N={candidate_count}) exceeds the limit of "
            f"{_MAX_CELL_EVALUATIONS}; no sampling is performed"
        )

    split_results: list[CpcvSplitResult] = []
    selected_columns: list[list[int]] = []
    for split in fold_model.splits:
        train = set(split.train_groups)
        group_train_rows = [row for row, group in enumerate(row_groups) if group in train]
        if purge_shared_backtest_windows:
            test = set(split.test_groups)
            test_windows = {
                matrix.window_indices[row] for row, group in enumerate(row_groups) if group in test
            }
            train_rows = [
                r for r in group_train_rows if matrix.window_indices[r] not in test_windows
            ]
        else:
            train_rows = group_train_rows
        if interval_rows is not None:
            test = set(split.test_groups)
            touched: set[int] = set()
            for rows in interval_rows:  # a position producing a return inside a test group
                if any(row_groups[r] in test for r in rows):
                    touched.update(rows)
            train_rows = [r for r in train_rows if r not in touched]
        if len(train_rows) < 2:
            raise ValueError(
                f"split {split.split_index} has {len(train_rows)} training row(s); at least 2 "
                "are required for a sample standard deviation"
            )
        sharpes = []
        for column, candidate_id in enumerate(matrix.candidate_ids):
            sharpe = _subsample_sharpe_ratio(
                [matrix.returns[row][column] for row in train_rows], risk_free_per_period
            )
            if sharpe is None:
                raise ValueError(
                    f"training Sharpe ratio is undefined for candidate {candidate_id!r} in split "
                    f"{split.split_index}: zero standard deviation"
                )
            sharpes.append(sharpe)
        best = max(sharpes)
        selected = [column for column, value in enumerate(sharpes) if value == best]
        selected_columns.append(selected)
        split_results.append(
            CpcvSplitResult(
                split_index=split.split_index,
                test_groups=split.test_groups,
                train_groups=split.train_groups,
                train_row_count=len(train_rows),
                train_sharpe_ratios=tuple(sharpes),
                selected_candidate_ids=tuple(matrix.candidate_ids[c] for c in selected),
                purged_train_row_count=len(group_train_rows) - len(train_rows),
            )
        )

    path_results: list[CpcvPathResult] = []
    for path_index, split_indices in enumerate(fold_model.path_split_indices):
        returns = []
        for row, group in enumerate(row_groups):
            columns = selected_columns[split_indices[group]]
            values = [matrix.returns[row][c] for c in columns]
            if len(values) == 1:
                returns.append(values[0])
            else:
                with _localcontext(_pbo_context()):
                    returns.append(sum(values, _Decimal(0)) / _Decimal(len(values)))
        sharpe = _subsample_sharpe_ratio(returns, risk_free_per_period)
        if sharpe is None:
            raise ValueError(
                f"path {path_index} Sharpe ratio is undefined: zero standard deviation"
            )
        path_results.append(
            CpcvPathResult(
                path_index=path_index,
                split_indices=split_indices,
                returns=tuple(returns),
                tie_averaged_groups=tuple(
                    g
                    for g, split_index in enumerate(split_indices)
                    if len(selected_columns[split_index]) > 1
                ),
                sharpe_ratio=sharpe,
            )
        )
    return CpcvResult(
        candidate_ids=matrix.candidate_ids,
        row_groups=tuple(row_groups),
        splits=tuple(split_results),
        paths=tuple(path_results),
    )


# ---------------------------------------------------------------- path distribution summary

DIAGNOSTIC_SCOPE = (
    "offline research diagnostic: descriptive summary of CPCV path Sharpe ratios; the paths "
    "reuse the same matrix observations (they are NOT independent samples); purging is "
    "window-level embargo only and no label/outcome-horizon purging is applied; no threshold, "
    "p-value or pass/fail; not a full CPCV validation and not a live strategy approval"
)


@_dataclass(frozen=True, slots=True)
class CpcvPathSummary:
    """Deterministic description of a `CpcvResult`'s path Sharpe ratios (Bölüm 17.2.20-17.2.24).

    `path_sharpe_ratios` follow `path_index`; `sorted_path_indices` are ascending
    by Sharpe ratio, equal values keeping path order. Mean, median and the
    sample (n - 1) standard deviation are computed in the fixed 28-digit
    Stage-2/PBO context.
    """

    diagnostic_scope: str
    path_count: int
    observations_per_path: int
    path_sharpe_ratios: tuple[_Decimal, ...]
    sorted_path_indices: tuple[int, ...]
    minimum_sharpe_ratio: _Decimal
    maximum_sharpe_ratio: _Decimal
    mean_sharpe_ratio: _Decimal
    median_sharpe_ratio: _Decimal
    sample_stdev_sharpe_ratio: _Decimal
    tie_averaged_path_count: int
    rows_identical_on_all_paths: int
    paths_share_observations: bool
    label_horizon_purging_applied: bool

    def __post_init__(self) -> None:
        for name in (
            "path_count",
            "observations_per_path",
            "tie_averaged_path_count",
            "rows_identical_on_all_paths",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
        for name in ("path_sharpe_ratios", "sorted_path_indices"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be a tuple, got {type(value).__name__}")
        for name in (
            "minimum_sharpe_ratio",
            "maximum_sharpe_ratio",
            "mean_sharpe_ratio",
            "median_sharpe_ratio",
            "sample_stdev_sharpe_ratio",
        ):
            value = getattr(self, name)
            if not isinstance(value, _Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal, got {value!r}")
        if self.label_horizon_purging_applied is not False:
            raise ValueError("label/outcome-horizon purging is not implemented (Bölüm 17.1.13)")


def summarize_cpcv_paths(result: CpcvResult) -> CpcvPathSummary:
    """Describe the path Sharpe distribution of `result` (Bölüm 17.2.20-17.2.24)."""
    if not isinstance(result, CpcvResult):
        raise TypeError(f"result must be a CpcvResult, got {type(result).__name__}")
    paths = result.paths
    if len(paths) < 2:
        raise ValueError(
            f"a path distribution needs at least 2 paths, got {len(paths)} "
            "(test_group_count = 1 is plain K-fold with a single path)"
        )
    if tuple(path.path_index for path in paths) != tuple(range(len(paths))):
        raise ValueError("paths must be ordered by path_index 0..P-1")
    row_count = len(result.row_groups)
    for path in paths:
        if len(path.returns) != row_count:
            raise ValueError(
                f"path {path.path_index} has {len(path.returns)} returns, expected {row_count} "
                "(one per matrix row)"
            )
    sharpes = [path.sharpe_ratio for path in paths]
    order = sorted(range(len(sharpes)), key=lambda index: sharpes[index])  # stable on ties
    ordered = [sharpes[index] for index in order]
    count = len(sharpes)
    with _localcontext(_pbo_context()):
        mean = sum(sharpes, _Decimal(0)) / _Decimal(count)
        middle = count // 2
        if count % 2:
            median = ordered[middle]
        else:
            median = (ordered[middle - 1] + ordered[middle]) / _Decimal(2)
        squared = sum(((value - mean) * (value - mean) for value in sharpes), _Decimal(0))
        stdev = (squared / _Decimal(count - 1)).sqrt()
    return CpcvPathSummary(
        diagnostic_scope=DIAGNOSTIC_SCOPE,
        path_count=count,
        observations_per_path=row_count,
        path_sharpe_ratios=tuple(sharpes),
        sorted_path_indices=tuple(order),
        minimum_sharpe_ratio=ordered[0],
        maximum_sharpe_ratio=ordered[-1],
        mean_sharpe_ratio=mean,
        median_sharpe_ratio=median,
        sample_stdev_sharpe_ratio=stdev,
        tie_averaged_path_count=sum(1 for path in paths if path.tie_averaged_groups),
        rows_identical_on_all_paths=sum(
            1 for row in range(row_count) if len({path.returns[row] for path in paths}) == 1
        ),
        paths_share_observations=True,  # every path covers every matrix row
        label_horizon_purging_applied=False,
    )
