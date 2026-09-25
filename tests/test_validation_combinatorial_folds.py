"""CPCV fold model (VALIDATION_SPEC.md Bölüm 17.2.1-17.2.10, 28.O).

Every expected split/path table below is derived by hand from the
lexicographic combination order; nothing is computed by the code under test.
"""

import ast
import inspect
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import crypto_quant_lab.validation as validation_package
from crypto_quant_lab.validation import combinatorial_folds as folds
from crypto_quant_lab.validation.combinatorial_folds import (
    CombinatorialFoldModel,
    CombinatorialSplit,
    build_combinatorial_fold_model,
)
from crypto_quant_lab.validation.windows import TemporalWindow

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
SRC = Path(folds.__file__).parents[1]


def groups(n, width=H, start=T0):
    return tuple(
        TemporalWindow(start=start + i * width, end=start + (i + 1) * width) for i in range(n)
    )


def table(model):
    return [(s.test_groups, s.train_groups, s.embargoed_groups) for s in model.splits]


# ================================================================ API / import direction


def test_public_api_and_signature_are_exact():
    public = {n for n in vars(folds) if not n.startswith("_") and n not in {
        "combinations", "comb", "dataclass", "timedelta", "purge_in_sample_windows",
        "TemporalWindow"}}  # fmt: skip
    assert public == {
        "CombinatorialSplit",
        "CombinatorialFoldModel",
        "build_combinatorial_fold_model",
    }
    signature = inspect.signature(build_combinatorial_fold_model)
    assert list(signature.parameters) == ["groups", "test_group_count", "embargo"]
    assert signature.parameters["test_group_count"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["embargo"].default == timedelta(0)
    assert list(CombinatorialSplit.__slots__) == [
        "split_index", "test_groups", "train_groups", "embargoed_groups", "test_windows",
        "train_windows"]  # fmt: skip
    assert list(CombinatorialFoldModel.__slots__) == [
        "groups", "test_group_count", "embargo", "splits", "path_split_indices"]  # fmt: skip


def test_import_direction_and_no_package_root_export():
    tree = ast.parse(Path(folds.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {
        a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names
    }
    assert imported == {"dataclasses", "datetime", "itertools", "math",
                        "crypto_quant_lab.validation.purging",
                        "crypto_quant_lab.validation.windows"}  # fmt: skip
    assert not hasattr(validation_package, "build_combinatorial_fold_model")
    importers = {
        path.name
        for path in SRC.rglob("*.py")
        if path.name != "combinatorial_folds.py"
        and "combinatorial_folds" in path.read_text(encoding="utf-8")
    }
    assert importers == {"cpcv.py", "cpcv_study.py"}  # §17.2.11 / §17.2.31 consumers


# ================================================================ splits and paths (hand-derived)


def test_six_groups_two_test_groups_splits_and_paths():
    model = build_combinatorial_fold_model(groups(6), test_group_count=2)
    pairs = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 2), (1, 3), (1, 4), (1, 5), (2, 3),
             (2, 4), (2, 5), (3, 4), (3, 5), (4, 5)]  # fmt: skip
    assert [s.test_groups for s in model.splits] == pairs  # C(6,2) = 15, lexicographic
    assert [s.split_index for s in model.splits] == list(range(15))
    assert model.splits[6].train_groups == (0, 2, 4, 5)
    # group g is tested in splits: g0 0-4; g1 0,5-8; g2 1,5,9-11; g3 2,6,9,12,13;
    # g4 3,7,10,12,14; g5 4,8,11,13,14 -> path p takes the p-th of each list
    assert model.path_split_indices == (
        (0, 0, 1, 2, 3, 4),
        (1, 5, 5, 6, 7, 8),
        (2, 6, 9, 9, 10, 11),
        (3, 7, 10, 12, 12, 13),
        (4, 8, 11, 13, 14, 14),
    )
    assert model.path_count == 5  # C(5,1) = k/N * C(N,k) = 2/6 * 15


def test_every_path_covers_each_group_once_from_a_split_that_tests_it():
    split_counts = {(4, 1): 4, (5, 2): 10, (6, 3): 20, (7, 2): 21, (8, 4): 70}  # C(N, k)
    path_counts = {(4, 1): 1, (5, 2): 4, (6, 3): 10, (7, 2): 6, (8, 4): 35}  # C(N-1, k-1)
    for (n, k), split_count in split_counts.items():
        model = build_combinatorial_fold_model(groups(n), test_group_count=k)
        assert (len(model.splits), model.path_count) == (split_count, path_counts[(n, k)])
        for path in model.path_split_indices:
            assert len(path) == n
            for group, split_index in enumerate(path):
                assert group in model.splits[split_index].test_groups
        for group in range(n):  # each (group, split) test use appears on exactly one path
            used = [path[group] for path in model.path_split_indices]
            assert sorted(used) == [s.split_index for s in model.splits if group in s.test_groups]


def test_k_equal_one_is_plain_k_fold_with_a_single_path():
    model = build_combinatorial_fold_model(groups(3), test_group_count=1)
    assert table(model) == [((0,), (1, 2), ()), ((1,), (0, 2), ()), ((2,), (0, 1), ())]
    assert model.path_split_indices == ((0, 1, 2),)


def test_windows_follow_the_group_indices():
    g = groups(4)
    model = build_combinatorial_fold_model(g, test_group_count=2)
    split = model.splits[4]  # (1, 3)
    assert split.test_windows == (g[1], g[3])
    assert split.train_windows == (g[0], g[2])
    assert all(a is b for a, b in zip(split.test_windows, (g[1], g[3]), strict=True))


# ================================================================ purge / embargo


def test_zero_embargo_trains_on_every_other_group_the_window_purge_never_fires():
    model = build_combinatorial_fold_model(groups(5), test_group_count=2)
    for split in model.splits:
        assert split.embargoed_groups == ()
        assert split.train_groups == tuple(g for g in range(5) if g not in split.test_groups)


def test_embargo_removes_only_groups_starting_inside_the_post_test_zone():
    half = build_combinatorial_fold_model(
        groups(4), test_group_count=1, embargo=timedelta(minutes=30)
    )
    assert table(half) == [((0,), (2, 3), (1,)), ((1,), (0, 3), (2,)), ((2,), (0, 1), (3,)),
                           ((3,), (0, 1, 2), ())]  # fmt: skip
    exact = build_combinatorial_fold_model(groups(4), test_group_count=1, embargo=H)
    assert table(exact) == table(half)  # zone [end, end+1h) touches only the next group
    longer = build_combinatorial_fold_model(
        groups(4), test_group_count=1, embargo=H + timedelta(microseconds=1)
    )
    assert table(longer) == [((0,), (3,), (1, 2)), ((1,), (0,), (2, 3)), ((2,), (0, 1), (3,)),
                             ((3,), (0, 1, 2), ())]  # fmt: skip
    groups_before_test_are_never_embargoed = all(
        all(g > min(s.test_groups) for g in s.embargoed_groups) for s in longer.splits
    )
    assert groups_before_test_are_never_embargoed


def test_embargo_after_each_of_several_test_groups():
    model = build_combinatorial_fold_model(
        groups(6), test_group_count=2, embargo=timedelta(minutes=1)
    )
    split = model.splits[6]  # test (1, 3): embargo zones start at g1.end and g3.end
    assert (split.train_groups, split.embargoed_groups) == ((0, 5), (2, 4))
    last = model.splits[14]  # test (4, 5): nothing follows the last group
    assert (last.train_groups, last.embargoed_groups) == ((0, 1, 2, 3), ())


def test_uses_the_existing_window_level_purge(monkeypatch):
    calls = []
    real = folds.purge_in_sample_windows

    def spy(windows, *, out_of_sample, embargo):
        calls.append((windows, out_of_sample, embargo))
        return real(windows, out_of_sample=out_of_sample, embargo=embargo)

    monkeypatch.setattr(folds, "purge_in_sample_windows", spy)
    g = groups(3)
    build_combinatorial_fold_model(g, test_group_count=1, embargo=H)
    assert (g[0],) in [c[0] for c in calls] and all(c[2] == H for c in calls)
    assert {c[1] for c in calls} == set(g)


def test_empty_training_set_after_embargo_is_an_error():
    with pytest.raises(ValueError, match=r"^split 0 \(test groups \[0, 1\]\) has no training "
                       r"group left after the embargo$"):  # fmt: skip
        build_combinatorial_fold_model(groups(3), test_group_count=2, embargo=H)


# ================================================================ validation (exact messages, order)


@pytest.mark.parametrize(
    ("kwargs", "error", "message"),
    [
        ({"groups": list(groups(3)), "test_group_count": 1}, TypeError,
         "groups must be a tuple, got list"),
        ({"groups": (groups(1)[0], "x"), "test_group_count": 1}, TypeError,
         "groups[1] must be a TemporalWindow, got str"),
        ({"groups": groups(3), "test_group_count": True}, TypeError,
         "test_group_count must be an int, got bool"),
        ({"groups": groups(3), "test_group_count": 1.0}, TypeError,
         "test_group_count must be an int, got float"),
        ({"groups": groups(3), "test_group_count": 1, "embargo": 3600}, TypeError,
         "embargo must be a timedelta, got int"),
        ({"groups": groups(3), "test_group_count": 1, "embargo": -H}, ValueError,
         "embargo must be >= timedelta(0), got datetime.timedelta(days=-1, seconds=82800)"),
        ({"groups": groups(1), "test_group_count": 1}, ValueError,
         "at least 2 groups are required, got 1"),
        ({"groups": (), "test_group_count": 1}, ValueError, "at least 2 groups are required, got 0"),
        ({"groups": groups(3), "test_group_count": 0}, ValueError,
         "test_group_count must be between 1 and 2 (N - 1), got 0"),
        ({"groups": groups(3), "test_group_count": 3}, ValueError,
         "test_group_count must be between 1 and 2 (N - 1), got 3"),
    ],
)  # fmt: skip
def test_invalid_inputs_have_exact_messages(kwargs, error, message):
    with pytest.raises(error) as caught:
        build_combinatorial_fold_model(**kwargs)
    assert str(caught.value) == message


@pytest.mark.parametrize(
    "bad",
    [
        (TemporalWindow(start=T0, end=T0 + H), TemporalWindow(start=T0 + 2 * H, end=T0 + 3 * H)),
        (TemporalWindow(start=T0, end=T0 + 2 * H), TemporalWindow(start=T0 + H, end=T0 + 3 * H)),
        (TemporalWindow(start=T0 + H, end=T0 + 2 * H), TemporalWindow(start=T0, end=T0 + H)),
    ],
    ids=["gap", "overlap", "reverse"],
)
def test_groups_must_be_a_contiguous_chronological_partition(bad):
    with pytest.raises(ValueError, match=r"^groups\[1\] must start exactly at groups\[0\]\.end "):
        build_combinatorial_fold_model(bad, test_group_count=1)


def test_equivalent_offsets_are_one_instant_and_unequal_widths_are_allowed():
    plus3 = timezone(timedelta(hours=3))
    mixed = (TemporalWindow(start=T0, end=T0 + H),
             TemporalWindow(start=(T0 + H).astimezone(plus3), end=T0 + 3 * H))  # fmt: skip
    model = build_combinatorial_fold_model(mixed, test_group_count=1)
    assert len(model.splits) == 2


def test_validation_order():
    with pytest.raises(TypeError, match="^groups must be a tuple"):
        build_combinatorial_fold_model([], test_group_count=True, embargo=-H)
    with pytest.raises(TypeError, match=r"^groups\[0\] must be"):
        build_combinatorial_fold_model(("x",), test_group_count=True)
    with pytest.raises(TypeError, match="^test_group_count must be an int"):
        build_combinatorial_fold_model((), test_group_count=None, embargo="x")
    with pytest.raises(TypeError, match="^embargo must be a timedelta"):
        build_combinatorial_fold_model((), test_group_count=5, embargo="x")
    with pytest.raises(ValueError, match="^embargo must be >="):
        build_combinatorial_fold_model((), test_group_count=5, embargo=-H)
    with pytest.raises(ValueError, match="^at least 2 groups"):
        build_combinatorial_fold_model(groups(1), test_group_count=5)
    gap = (groups(1)[0], TemporalWindow(start=T0 + 2 * H, end=T0 + 3 * H))
    with pytest.raises(ValueError, match="must start exactly"):
        build_combinatorial_fold_model(gap, test_group_count=5)


def test_split_limit_is_checked_before_any_split_is_built(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no split may be built")

    monkeypatch.setattr(folds, "purge_in_sample_windows", refuse)
    with pytest.raises(ValueError) as caught:
        build_combinatorial_fold_model(groups(20), test_group_count=10)
    assert str(caught.value) == (
        "C(N=20, k=10) = 184756 splits exceeds the limit of 100000; no sampling is performed"
    )


def test_split_limit_is_inclusive(monkeypatch):
    monkeypatch.setattr(folds, "_MAX_SPLITS", 15)
    assert len(build_combinatorial_fold_model(groups(6), test_group_count=2).splits) == 15
    monkeypatch.setattr(folds, "_MAX_SPLITS", 14)
    with pytest.raises(ValueError, match=r"= 15 splits exceeds the limit of 14"):
        build_combinatorial_fold_model(groups(6), test_group_count=2)


# ================================================================ purity / value objects


def test_deterministic_and_inputs_untouched():
    g = groups(5)
    snapshot = tuple((w.start, w.end) for w in g)
    first = build_combinatorial_fold_model(g, test_group_count=2, embargo=timedelta(minutes=5))
    second = build_combinatorial_fold_model(g, test_group_count=2, embargo=timedelta(minutes=5))
    assert first == second
    assert tuple((w.start, w.end) for w in g) == snapshot
    assert first.groups is g


def test_value_objects_validate_their_fields():
    g = groups(2)
    good = {"split_index": 0, "test_groups": (0,), "train_groups": (1,), "embargoed_groups": (),
            "test_windows": (g[0],), "train_windows": (g[1],)}  # fmt: skip
    CombinatorialSplit(**good)
    with pytest.raises(TypeError, match="^split_index must be an int, got bool$"):
        CombinatorialSplit(**good | {"split_index": True})
    with pytest.raises(TypeError, match=r"^test_groups\[0\] must be an int, got str$"):
        CombinatorialSplit(**good | {"test_groups": ("0",)})
    with pytest.raises(ValueError, match="^train_windows must have one window per train group$"):
        CombinatorialSplit(**good | {"train_windows": ()})
    split = CombinatorialSplit(**good)
    with pytest.raises(TypeError, match=r"^splits\[0\] must be a CombinatorialSplit, got str$"):
        CombinatorialFoldModel(groups=g, test_group_count=1, embargo=timedelta(0),
                               splits=("x",), path_split_indices=((0,),))  # fmt: skip
    with pytest.raises(TypeError, match=r"^path_split_indices\[0\] must be a tuple, got list$"):
        CombinatorialFoldModel(groups=g, test_group_count=1, embargo=timedelta(0),
                               splits=(split,), path_split_indices=([0],))  # fmt: skip
