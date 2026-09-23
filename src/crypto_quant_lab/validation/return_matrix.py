"""Trial return matrix foundation (VALIDATION_SPEC.md Bölüm 17.5.1-17.5.12, 28.L).

The T x N performance matrix that Bailey, Borwein, López de Prado & Zhu's CSCV
(Algorithm 2.3) takes as input: one column per trial of a `TrialGroup`, one
row per synchronous observation, each row labelled with the evaluation window
it came from. Cells are the existing per-observation simple returns (Bölüm
15.13), reused unchanged.

This is a foundation only: no PBO, no CSCV partitioning, no selection or
ranking, no purging. Missing or misaligned observations are rejected, never
filled, dropped or averaged. The matrix proves nothing about cost-model
equality, leakage, independence or holdout protection.
"""

from dataclasses import dataclass as _dataclass
from datetime import datetime as _datetime
from decimal import Decimal as _Decimal

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us as _datetime_to_epoch_us
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns as _compute_periodic_returns,
)
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup


@_dataclass(frozen=True, slots=True)
class TrialReturnMatrix:
    """Row-major T x N matrix: `returns[t][n]` is trial `n`'s return at observation `t`.

    Columns follow `candidate_ids`; rows follow `observation_times`, grouped
    into contiguous evaluation-window blocks by `window_indices`.
    """

    candidate_ids: tuple[str, ...]
    observation_times: tuple[_datetime, ...]
    window_indices: tuple[int, ...]
    returns: tuple[tuple[_Decimal, ...], ...]

    def __post_init__(self) -> None:
        """Enforce Bölüm 17.5.4's exact order; every multi-element stage is a global pass."""
        candidate_ids = self.candidate_ids
        if not isinstance(candidate_ids, tuple):
            raise TypeError(f"candidate_ids must be a tuple, got {type(candidate_ids).__name__}")
        if len(candidate_ids) == 0:
            raise ValueError("candidate_ids must not be empty")
        for index, candidate_id in enumerate(candidate_ids):
            if not isinstance(candidate_id, str):
                raise TypeError(
                    f"candidate_ids[{index}] must be a str, got {type(candidate_id).__name__}"
                )
        first_index: dict[str, int] = {}
        for index, candidate_id in enumerate(candidate_ids):
            if candidate_id in first_index:
                raise ValueError(
                    f"candidate_ids[{index}] {candidate_id!r} duplicates "
                    f"candidate_ids[{first_index[candidate_id]}]"
                )
            first_index[candidate_id] = index

        times = self.observation_times
        if not isinstance(times, tuple):
            raise TypeError(f"observation_times must be a tuple, got {type(times).__name__}")
        if len(times) == 0:
            raise ValueError("observation_times must not be empty")
        for index, time in enumerate(times):
            if not isinstance(time, _datetime):
                raise TypeError(
                    f"observation_times[{index}] must be a datetime, got {type(time).__name__}"
                )
        epochs = [_datetime_to_epoch_us(time) for time in times]
        for index in range(1, len(epochs)):
            if epochs[index] <= epochs[index - 1]:
                raise ValueError(
                    f"observation_times[{index}] must be after observation_times[{index - 1}]"
                )

        row_count = len(times)
        window_indices = self.window_indices
        if not isinstance(window_indices, tuple):
            raise TypeError(f"window_indices must be a tuple, got {type(window_indices).__name__}")
        if len(window_indices) != row_count:
            raise ValueError(
                f"window_indices must have {row_count} entries, got {len(window_indices)}"
            )
        for index, window_index in enumerate(window_indices):
            if isinstance(window_index, bool) or not isinstance(window_index, int):
                raise TypeError(
                    f"window_indices[{index}] must be an int, got {type(window_index).__name__}"
                )
        if window_indices[0] != 0:
            raise ValueError(f"window_indices[0] must be 0, got {window_indices[0]}")
        for index in range(1, row_count):
            previous = window_indices[index - 1]
            if window_indices[index] not in (previous, previous + 1):
                raise ValueError(
                    f"window_indices[{index}] must equal window_indices[{index - 1}] or "
                    f"window_indices[{index - 1}] + 1, got {window_indices[index]}"
                )

        column_count = len(candidate_ids)
        returns = self.returns
        if not isinstance(returns, tuple):
            raise TypeError(f"returns must be a tuple, got {type(returns).__name__}")
        if len(returns) != row_count:
            raise ValueError(f"returns must have {row_count} rows, got {len(returns)}")
        for row_index, row in enumerate(returns):
            if not isinstance(row, tuple):
                raise TypeError(f"returns[{row_index}] must be a tuple, got {type(row).__name__}")
        for row_index, row in enumerate(returns):
            if len(row) != column_count:
                raise ValueError(
                    f"returns[{row_index}] must have {column_count} columns, got {len(row)}"
                )
        for row_index, row in enumerate(returns):
            for column_index, cell in enumerate(row):
                if not isinstance(cell, _Decimal):
                    raise TypeError(
                        f"returns[{row_index}][{column_index}] must be a Decimal, "
                        f"got {type(cell).__name__}"
                    )
        for row_index, row in enumerate(returns):
            for column_index, cell in enumerate(row):
                if not cell.is_finite():
                    raise ValueError(
                        f"returns[{row_index}][{column_index}] must be finite, got {cell}"
                    )


def build_trial_return_matrix(group: _TrialGroup) -> TrialReturnMatrix:
    """Build the aligned T x N return matrix of `group` (Bölüm 17.5.5)."""
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")  # 1

    trials = group.trials
    reference_results = trials[0].results
    windows = tuple(window_result.window for window_result in reference_results)

    # 2: chronological, non-overlapping evaluation windows.
    for index in range(1, len(windows)):
        if windows[index].start < windows[index - 1].end:
            raise ValueError(
                "evaluation windows must be chronologically ordered and non-overlapping: "
                f"windows[{index}].start ({windows[index].start!r}) is before "
                f"windows[{index - 1}].end ({windows[index - 1].end!r})"
            )

    # 3: every trials[0] observation belongs to its own window. Equity points are
    # stamped at feature_availability_time (candle close), so candles opening in
    # [start, end) yield observation times in (start, end].
    for window_index, window_result in enumerate(reference_results):
        window = window_result.window
        for point_index, point in enumerate(window_result.result.equity_curve):
            if not (window.start < point.time <= window.end):
                raise ValueError(
                    f"trials[0].results[{window_index}].result.equity_curve[{point_index}].time "
                    f"({point.time!r}) is outside the observation range "
                    f"({window.start!r}, {window.end!r}] of its evaluation window"
                )

    # 4: synchronous observations across trials.
    reference_times = [
        tuple(point.time for point in window_result.result.equity_curve)
        for window_result in reference_results
    ]
    for trial_index in range(1, len(trials)):
        for window_index, window_result in enumerate(trials[trial_index].results):
            times = tuple(point.time for point in window_result.result.equity_curve)
            if times != reference_times[window_index]:
                raise ValueError(
                    f"trials[{trial_index}].results[{window_index}] equity observation times "
                    f"do not match trials[0].results[{window_index}]"
                )

    # 5: existing periodic returns, errors propagate unchanged.
    columns = [
        [_compute_periodic_returns(window_result.result) for window_result in trial.results]
        for trial in trials
    ]

    # 6: row-major assembly in window order, then observation order.
    observation_times: list[_datetime] = []
    window_indices: list[int] = []
    rows: list[tuple[_Decimal, ...]] = []
    for window_index, times in enumerate(reference_times):
        for point_index, time in enumerate(times):
            observation_times.append(time)
            window_indices.append(window_index)
            rows.append(tuple(column[window_index][point_index] for column in columns))

    return TrialReturnMatrix(
        candidate_ids=tuple(trial.candidate.candidate_id for trial in trials),
        observation_times=tuple(observation_times),
        window_indices=tuple(window_indices),
        returns=tuple(rows),
    )
