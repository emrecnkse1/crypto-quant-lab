"""CPCV fold model: combinatorial train/test splits and backtest paths (VALIDATION_SPEC.md Bölüm 17.2.1-17.2.10, 28.O).

The evaluation span is given as N contiguous, chronological `TemporalWindow`
groups. For every one of the C(N, k) combinations of k test groups (in
lexicographic order) the remaining groups are training candidates; a training
group is dropped when it lies in the post-test embargo zone of any test group,
using the existing window-level `purge_in_sample_windows` (Bölüm 17.1). Every
group is a test group in exactly C(N-1, k-1) splits; path p takes, for each
group, the p-th of those splits (ascending split index), so each of the
C(N-1, k-1) paths covers every group exactly once.

This is ONLY the fold model prerequisite of CPCV (Bölüm 17.2). It evaluates
nothing: no backtest, no returns, no Sharpe, no candidate selection. Groups
are windows, not observations, so the window-level purge can never remove a
training group of a contiguous partition; label/outcome-horizon purging stays
deferred (Bölüm 17.1.13) and only the explicit post-test embargo acts.
"""

from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations
from math import comb

from crypto_quant_lab.validation.purging import purge_in_sample_windows
from crypto_quant_lab.validation.windows import TemporalWindow

_MAX_SPLITS = 100_000


def _require_index_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"{field_name}[{index}] must be an int, got {type(item).__name__}")


def _require_window_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, TemporalWindow):
            raise TypeError(
                f"{field_name}[{index}] must be a TemporalWindow, got {type(item).__name__}"
            )


@dataclass(frozen=True, slots=True)
class CombinatorialSplit:
    """One train/test split; group indices refer to `CombinatorialFoldModel.groups`.

    `test_groups`, `train_groups` and `embargoed_groups` are ascending and
    together partition every group index exactly once.
    """

    split_index: int
    test_groups: tuple[int, ...]
    train_groups: tuple[int, ...]
    embargoed_groups: tuple[int, ...]
    test_windows: tuple[TemporalWindow, ...]
    train_windows: tuple[TemporalWindow, ...]

    def __post_init__(self) -> None:
        if isinstance(self.split_index, bool) or not isinstance(self.split_index, int):
            raise TypeError(f"split_index must be an int, got {type(self.split_index).__name__}")
        for field_name in ("test_groups", "train_groups", "embargoed_groups"):
            _require_index_tuple(getattr(self, field_name), field_name)
        for field_name in ("test_windows", "train_windows"):
            _require_window_tuple(getattr(self, field_name), field_name)
        if len(self.test_windows) != len(self.test_groups):
            raise ValueError("test_windows must have one window per test group")
        if len(self.train_windows) != len(self.train_groups):
            raise ValueError("train_windows must have one window per train group")


@dataclass(frozen=True, slots=True)
class CombinatorialFoldModel:
    """All C(N, k) splits plus the C(N-1, k-1) paths.

    `path_split_indices[p][g]` is the split whose test set supplies group g on
    path p.
    """

    groups: tuple[TemporalWindow, ...]
    test_group_count: int
    embargo: timedelta
    splits: tuple[CombinatorialSplit, ...]
    path_split_indices: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        _require_window_tuple(self.groups, "groups")
        if isinstance(self.test_group_count, bool) or not isinstance(self.test_group_count, int):
            raise TypeError(
                f"test_group_count must be an int, got {type(self.test_group_count).__name__}"
            )
        if not isinstance(self.embargo, timedelta):
            raise TypeError(f"embargo must be a timedelta, got {type(self.embargo).__name__}")
        if not isinstance(self.splits, tuple):
            raise TypeError(f"splits must be a tuple, got {type(self.splits).__name__}")
        for index, split in enumerate(self.splits):
            if not isinstance(split, CombinatorialSplit):
                raise TypeError(
                    f"splits[{index}] must be a CombinatorialSplit, got {type(split).__name__}"
                )
        if not isinstance(self.path_split_indices, tuple):
            raise TypeError(
                f"path_split_indices must be a tuple, got {type(self.path_split_indices).__name__}"
            )
        for index, path in enumerate(self.path_split_indices):
            _require_index_tuple(path, f"path_split_indices[{index}]")

    @property
    def path_count(self) -> int:
        return len(self.path_split_indices)


def build_combinatorial_fold_model(
    groups: tuple[TemporalWindow, ...],
    *,
    test_group_count: int,
    embargo: timedelta = timedelta(0),
) -> CombinatorialFoldModel:
    """Build the CPCV fold model (VALIDATION_SPEC.md Bölüm 17.2.4-17.2.7).

    Validation order: groups type, every group's type (global pass),
    test_group_count type, embargo type, embargo >= 0, at least two groups,
    contiguous chronological partition, 1 <= k <= N - 1, C(N, k) <= limit;
    then an empty training set after the embargo is an error.
    """
    _require_window_tuple(groups, "groups")
    if isinstance(test_group_count, bool) or not isinstance(test_group_count, int):
        raise TypeError(f"test_group_count must be an int, got {type(test_group_count).__name__}")
    if not isinstance(embargo, timedelta):
        raise TypeError(f"embargo must be a timedelta, got {type(embargo).__name__}")
    if embargo < timedelta(0):
        raise ValueError(f"embargo must be >= timedelta(0), got {embargo!r}")
    group_count = len(groups)
    if group_count < 2:
        raise ValueError(f"at least 2 groups are required, got {group_count}")
    for index in range(1, group_count):
        if groups[index].start != groups[index - 1].end:
            raise ValueError(
                f"groups[{index}] must start exactly at groups[{index - 1}].end "
                "(contiguous, chronological, non-overlapping partition; no gaps)"
            )
    if not 1 <= test_group_count <= group_count - 1:
        raise ValueError(
            f"test_group_count must be between 1 and {group_count - 1} (N - 1), "
            f"got {test_group_count}"
        )
    split_count = comb(group_count, test_group_count)
    if split_count > _MAX_SPLITS:
        raise ValueError(
            f"C(N={group_count}, k={test_group_count}) = {split_count} splits exceeds the limit "
            f"of {_MAX_SPLITS}; no sampling is performed"
        )

    splits: list[CombinatorialSplit] = []
    for split_index, test_groups in enumerate(combinations(range(group_count), test_group_count)):
        candidates = tuple(g for g in range(group_count) if g not in test_groups)
        train_groups = tuple(
            g
            for g in candidates
            if all(
                purge_in_sample_windows((groups[g],), out_of_sample=groups[t], embargo=embargo)
                for t in test_groups
            )
        )
        if not train_groups:
            raise ValueError(
                f"split {split_index} (test groups {list(test_groups)}) has no training group "
                "left after the embargo"
            )
        splits.append(
            CombinatorialSplit(
                split_index=split_index,
                test_groups=test_groups,
                train_groups=train_groups,
                embargoed_groups=tuple(g for g in candidates if g not in train_groups),
                test_windows=tuple(groups[g] for g in test_groups),
                train_windows=tuple(groups[g] for g in train_groups),
            )
        )

    tested_in = [
        [split.split_index for split in splits if group in split.test_groups]
        for group in range(group_count)
    ]
    path_count = comb(group_count - 1, test_group_count - 1)
    paths = tuple(
        tuple(tested_in[group][path] for group in range(group_count)) for path in range(path_count)
    )
    return CombinatorialFoldModel(
        groups=groups,
        test_group_count=test_group_count,
        embargo=embargo,
        splits=tuple(splits),
        path_split_indices=paths,
    )
