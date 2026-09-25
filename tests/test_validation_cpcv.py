"""CPCV path returns with training-set selection (VALIDATION_SPEC.md Bölüm 17.2.11-17.2.19, 28.P).

Selections and path returns are derived by hand in the comments (a two-row
training Sharpe ratio is sqrt(2) * mean / |difference|, so the argmax is read
from mean / |difference|); Sharpe values are checked against an independent
Fraction-based reference, never against the code under test.
"""

import ast
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest
import test_validation_pbo as pbo_tests

import crypto_quant_lab.validation as validation_package
from crypto_quant_lab.validation import cpcv
from crypto_quant_lab.validation import pbo as pbo_module
from crypto_quant_lab.validation.combinatorial_folds import build_combinatorial_fold_model
from crypto_quant_lab.validation.cpcv import (
    CpcvPathResult,
    CpcvPathSummary,
    CpcvResult,
    CpcvSplitResult,
    compute_cpcv_paths,
    summarize_cpcv_paths,
)
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix, build_trial_return_matrix
from crypto_quant_lab.validation.windows import TemporalWindow

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
U = Decimal("0.01")
TOLERANCE = Decimal("1e-25")
# main fixture (units of 0.01): one row per group, rows 0..3 = groups 0..3
MAIN = {"A": (1, 2, 5, 3), "B": (3, 1, 2, 6), "C": (2, 5, 1, 7)}
# tie fixture: E equals D except on row 3
TIES = {"A": (1, 2, 5, 3), "D": (3, 1, 2, 6), "E": (3, 1, 2, 8)}


def groups(n, width=H):
    return tuple(TemporalWindow(start=T0 + i * width, end=T0 + (i + 1) * width) for i in range(n))


def matrix(columns, rows_per_group=1, width=H):
    ids = tuple(columns)
    count = len(next(iter(columns.values())))
    step = width / rows_per_group
    return TrialReturnMatrix(
        candidate_ids=ids,
        observation_times=tuple(T0 + step * (t + 1) for t in range(count)),
        window_indices=(0,) * count,
        returns=tuple(tuple(Decimal(columns[c][t]) * U for c in ids) for t in range(count)),
    )


def run(columns, n=4, k=2, embargo=timedelta(0), rows_per_group=1, **kwargs):
    model = build_combinatorial_fold_model(groups(n), test_group_count=k, embargo=embargo)
    return compute_cpcv_paths(matrix(columns, rows_per_group), model, **kwargs)


def reference_sharpe(values, rf=Decimal(0)):
    """Independent sample Sharpe: exact Fraction mean/variance, 50-digit sqrt."""
    fractions = [Fraction(v) for v in values]
    mean = sum(fractions) / len(fractions)
    variance = sum((x - mean) ** 2 for x in fractions) / (len(fractions) - 1)
    with localcontext(Context(prec=50)):
        stdev = (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
        return (Decimal(mean.numerator) / Decimal(mean.denominator) - rf) / stdev


def close(value, expected):
    return abs(value - expected) <= TOLERANCE


def units(values):
    return tuple(Decimal(v) * U for v in values)


# ================================================================ API / reuse / import direction


def test_public_api_and_reuse_of_the_pbo_rules():
    public = {n for n in vars(cpcv) if not n.startswith("_")}
    assert public == {"CpcvSplitResult", "CpcvPathResult", "CpcvResult", "compute_cpcv_paths",
                      "CpcvPathSummary", "summarize_cpcv_paths", "DIAGNOSTIC_SCOPE"}  # fmt: skip
    signature = inspect.signature(compute_cpcv_paths)
    assert list(signature.parameters) == [
        "matrix", "fold_model", "risk_free_per_period", "purge_shared_backtest_windows"]  # fmt: skip
    assert signature.parameters["purge_shared_backtest_windows"].default is False
    assert signature.parameters["risk_free_per_period"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["risk_free_per_period"].default == Decimal(0)
    # the Stage-2-identical subsample Sharpe of PBO is reused, not copied
    assert cpcv._subsample_sharpe_ratio is pbo_module._subsample_sharpe_ratio
    assert cpcv._pbo_context is pbo_module._pbo_context


def test_import_direction_and_no_package_root_export():
    tree = ast.parse(Path(cpcv.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported == {"dataclasses", "decimal", "crypto_quant_lab.validation.combinatorial_folds",
                        "crypto_quant_lab.validation.pbo",
                        "crypto_quant_lab.validation.return_matrix"}  # fmt: skip
    assert not hasattr(validation_package, "compute_cpcv_paths")
    importers = {
        path.name
        for path in Path(cpcv.__file__).parent.rglob("*.py")
        if path.name != "cpcv.py" and "validation.cpcv import" in path.read_text(encoding="utf-8")
    }
    assert importers == {"cpcv_study.py"}  # the §17.2.31 study is the only consumer


# ================================================================ hand-derived selections and paths


def test_different_splits_select_different_candidates():
    result = run(MAIN)
    # split: test groups | train rows -> mean/|diff| per candidate -> winner
    # 0 (0,1) | 2,3 -> A 4/2=2,     B 4/4=1,      C 4/6=2/3  -> A
    # 1 (0,2) | 1,3 -> A 2.5/1=5/2, B 3.5/5=7/10, C 6/2=3    -> C
    # 2 (0,3) | 1,2 -> A 3.5/3=7/6, B 1.5/1=3/2,  C 3/4      -> B
    # 3 (1,2) | 0,3 -> A 2/2=1,     B 4.5/3=3/2,  C 4.5/5    -> B
    # 4 (1,3) | 0,2 -> A 3/4,       B 2.5/1=5/2,  C 1.5/1    -> B
    # 5 (2,3) | 0,1 -> A 1.5/1=3/2, B 2/2=1,      C 3.5/3    -> A
    assert [s.selected_candidate_ids for s in result.splits] == [
        ("A",), ("C",), ("B",), ("B",), ("B",), ("A",)]  # fmt: skip
    assert [s.train_groups for s in result.splits] == [
        (2, 3),
        (1, 3),
        (1, 2),
        (0, 3),
        (0, 2),
        (0, 1),
    ]
    assert all(s.train_row_count == 2 for s in result.splits)
    root2 = Decimal(2).sqrt()
    assert close(result.splits[0].train_sharpe_ratios[0], root2 * 2)  # A: sqrt(2) * 2
    assert close(result.splits[1].train_sharpe_ratios[2], root2 * 3)  # C: sqrt(2) * 3
    for split in result.splits:  # every training Sharpe against the independent reference
        train_rows = [g for g in split.train_groups]
        for column, candidate in enumerate(MAIN):
            expected = reference_sharpe([MAIN[candidate][r] * U for r in train_rows])
            assert close(split.train_sharpe_ratios[column], expected)


def test_path_returns_take_each_group_from_its_assigned_split():
    result = run(MAIN)
    # paths (C(3,1) = 3): p0 = s0,s0,s1,s2; p1 = s1,s3,s3,s4; p2 = s2,s4,s5,s5
    assert [p.split_indices for p in result.paths] == [(0, 0, 1, 2), (1, 3, 3, 4), (2, 4, 5, 5)]
    # p0: A[0]=1, A[1]=2, C[2]=1, B[3]=6; p1: C[0]=2, B[1]=1, B[2]=2, B[3]=6;
    # p2: B[0]=3, B[1]=1, A[2]=5, A[3]=3
    assert [p.returns for p in result.paths] == [units((1, 2, 1, 6)), units((2, 1, 2, 6)),
                                                 units((3, 1, 5, 3))]  # fmt: skip
    assert all(p.tie_averaged_groups == () for p in result.paths)
    # p2: mean 3, sample variance 8/3 -> Sharpe 3 / sqrt(8/3) = 3*sqrt(6)/4
    assert close(result.paths[2].sharpe_ratio, Decimal(3) * Decimal(6).sqrt() / Decimal(4))
    assert close(result.paths[0].sharpe_ratio, reference_sharpe(units((1, 2, 1, 6))))  # ~1.0502
    assert close(result.paths[1].sharpe_ratio, reference_sharpe(units((2, 1, 2, 6))))  # ~1.2402
    assert result.row_groups == (0, 1, 2, 3)


def test_every_path_row_is_the_selected_return_of_its_split():
    columns = {"A": (1, 2, 5, 3, 4, 7), "B": (3, 1, 2, 6, 5, 4), "C": (2, 5, 1, 7, 3, 6)}
    result = run(columns, n=6, k=3)
    ids = list(columns)
    for path in result.paths:
        for row, group in enumerate(result.row_groups):
            split = result.splits[path.split_indices[group]]
            assert group in split.test_groups
            chosen = [Decimal(columns[c][row]) * U for c in split.selected_candidate_ids]
            assert path.returns[row] == sum(chosen) / len(chosen)
            assert set(split.selected_candidate_ids) <= set(ids)


# ================================================================ no leakage from test rows


def test_test_and_embargoed_rows_never_enter_the_selection():
    base = run(MAIN)
    for split in base.splits:
        hostile = {c: list(v) for c, v in MAIN.items()}
        for row in range(4):
            if row not in split.train_groups:  # test rows of this split
                hostile["A"][row] = -90 - row  # distinct values: every other split
                hostile["B"][row] = 95 + row  # keeps a defined training Sharpe
                hostile["C"][row] = 40 + 3 * row
        changed = run(hostile)
        again = changed.splits[split.split_index]
        assert again.train_sharpe_ratios == split.train_sharpe_ratios
        assert again.selected_candidate_ids == split.selected_candidate_ids


def test_test_results_do_not_change_the_selection_afterwards():
    # split 0 selects A on rows 2,3; A is then awful on its test rows 0,1
    hostile = {c: list(v) for c, v in MAIN.items()}
    hostile["A"][0], hostile["A"][1] = -50, -49
    result = run(hostile)
    assert result.splits[0].selected_candidate_ids == ("A",)
    assert result.paths[0].returns[:2] == units((-50, -49))  # read, not re-selected


def test_embargoed_rows_are_excluded_from_training():
    columns = {"A": (1, 2, 5, 3), "B": (3, 1, 2, 6)}
    base = run(columns, k=1, embargo=timedelta(microseconds=1))
    # k=1, embargo: test {0} -> embargoed {1}, train {2,3}
    assert (base.splits[0].train_groups, base.splits[0].train_row_count) == ((2, 3), 2)
    hostile = {"A": (1, -80, 5, 3), "B": (3, 90, 2, 6)}  # only the embargoed row 1 differs
    changed = run(hostile, k=1, embargo=timedelta(microseconds=1))
    assert changed.splits[0].train_sharpe_ratios == base.splits[0].train_sharpe_ratios
    assert changed.splits[0].selected_candidate_ids == base.splits[0].selected_candidate_ids


# ================================================================ ties (PBO rule)


def test_tied_training_winners_are_all_selected_and_averaged_on_the_path():
    result = run(TIES)
    # D and E are identical except row 3: splits whose training rows exclude 3 tie
    # s0 (train 2,3) A 2; s1 (1,3) A 5/2; s2 (1,2) D=E 3/2 > A 7/6; s3 (0,3) D 3/2 > E 11/10 > A 1;
    # s4 (0,2) D=E 5/2 > A 3/4; s5 (0,1) A 3/2 > D=E 1
    assert [s.selected_candidate_ids for s in result.splits] == [
        ("A",), ("A",), ("D", "E"), ("D",), ("D", "E"), ("A",)]  # fmt: skip
    # p0 = s0,s0,s1,s2 -> A1, A2, A5, (6+8)/2=7 ; p1 = s1,s3,s3,s4 -> A1, D1, D2, 7 ;
    # p2 = s2,s4,s5,s5 -> (3+3)/2=3, (1+1)/2=1, A5, A3
    assert [p.returns for p in result.paths] == [units((1, 2, 5, 7)), units((1, 1, 2, 7)),
                                                 units((3, 1, 5, 3))]  # fmt: skip
    assert [p.tie_averaged_groups for p in result.paths] == [(3,), (3,), (0, 1)]


def test_candidate_order_does_not_change_selection_or_paths():
    reordered = run({c: TIES[c] for c in ("E", "A", "D")})
    base = run(TIES)
    assert [set(s.selected_candidate_ids) for s in reordered.splits] == [
        set(s.selected_candidate_ids) for s in base.splits]  # fmt: skip
    assert [p.returns for p in reordered.paths] == [p.returns for p in base.paths]
    assert [reordered.splits[2].selected_candidate_ids] == [("E", "D")]  # column order


# ================================================================ invalid / insufficient data


@pytest.mark.parametrize(
    ("call", "error", "message"),
    [
        (lambda m, f: compute_cpcv_paths("m", f), TypeError,
         "matrix must be a TrialReturnMatrix, got str"),
        (lambda m, f: compute_cpcv_paths(m, "f"), TypeError,
         "fold_model must be a CombinatorialFoldModel, got str"),
        (lambda m, f: compute_cpcv_paths(m, f, risk_free_per_period=0), TypeError,
         "risk_free_per_period must be a Decimal, got int"),
        (lambda m, f: compute_cpcv_paths(m, f, risk_free_per_period=Decimal("NaN")), ValueError,
         "risk_free_per_period must be finite, got NaN"),
    ],
)  # fmt: skip
def test_invalid_arguments_have_exact_messages(call, error, message):
    model = build_combinatorial_fold_model(groups(4), test_group_count=2)
    with pytest.raises(error) as caught:
        call(matrix(MAIN), model)
    assert str(caught.value) == message


def test_one_candidate_is_rejected():
    with pytest.raises(ValueError, match="^at least two candidates are required for a training"
                       "-set selection, got 1$"):  # fmt: skip
        run({"A": MAIN["A"]})


def test_rows_outside_the_groups_are_rejected_never_dropped():
    model = build_combinatorial_fold_model(groups(4), test_group_count=2)
    late = TrialReturnMatrix(candidate_ids=("A", "B"),
                             observation_times=tuple(T0 + H * (t + 1) for t in range(5)),
                             window_indices=(0,) * 5,
                             returns=tuple((U, U * 2) for _ in range(5)))  # fmt: skip
    with pytest.raises(ValueError) as caught:
        compute_cpcv_paths(late, model)
    assert str(caught.value) == (
        "matrix row 4 at 2026-01-01T05:00:00+00:00 is outside every fold group "
        "(ownership is start < time <= end); rows are never dropped"
    )
    at_start = TrialReturnMatrix(candidate_ids=("A", "B"),
                                 observation_times=tuple(T0 + H * t for t in range(4)),
                                 window_indices=(0,) * 4,
                                 returns=tuple((U, U * 2) for _ in range(4)))  # fmt: skip
    with pytest.raises(ValueError, match=r"^matrix row 0 at 2026-01-01T00:00:00\+00:00 is outside"):
        compute_cpcv_paths(at_start, model)  # a group's start belongs to the previous group


def test_a_group_without_rows_is_rejected():
    model = build_combinatorial_fold_model(groups(8, width=H / 2), test_group_count=2)
    with pytest.raises(ValueError, match="^fold group 0 owns no matrix row$"):
        compute_cpcv_paths(matrix(MAIN), model)  # rows at +1h, +2h, ... own odd half-hours


def test_fewer_than_two_training_rows_is_an_error():
    with pytest.raises(ValueError, match=r"^split 0 has 1 training row\(s\); at least 2 are "
                       "required for a sample standard deviation$"):  # fmt: skip
        run({"A": (1, 2, 5), "B": (3, 1, 2)}, n=3, k=1, embargo=timedelta(microseconds=1))


def test_zero_training_stdev_is_an_error_not_zero_or_infinity():
    with pytest.raises(ValueError, match=r"^training Sharpe ratio is undefined for candidate 'B' "
                       "in split 0: zero standard deviation$"):  # fmt: skip
        run({"A": (1, 2, 5, 3), "B": (3, 1, 4, 4)})


def test_zero_path_stdev_is_an_error():
    # N=3, k=1, two rows per group; each candidate is (0, 0) only on "its" group and wins
    # the split whose TEST group that is, so the single path is all zeros
    columns = {"X": (0, 0, 5, 6, 5, 6), "Y": (5, 6, 0, 0, 5, 6), "Z": (5, 6, 5, 6, 0, 0)}
    model = build_combinatorial_fold_model(groups(3), test_group_count=1)
    with pytest.raises(ValueError, match="^path 0 Sharpe ratio is undefined: zero standard "
                       "deviation$"):  # fmt: skip
        compute_cpcv_paths(matrix(columns, rows_per_group=2), model)


def test_cost_limit_is_checked_before_any_selection(monkeypatch):
    monkeypatch.setattr(cpcv, "_MAX_CELL_EVALUATIONS", 71)  # 6 splits x 4 rows x 3 = 72

    def refuse(*args, **kwargs):
        raise AssertionError("no Sharpe may be computed")

    monkeypatch.setattr(cpcv, "_subsample_sharpe_ratio", refuse)
    with pytest.raises(ValueError) as caught:
        run(MAIN)
    assert str(caught.value) == (
        "CPCV cost of 72 cell evaluations (splits=6 x T=4 x N=3) exceeds the limit of 71; "
        "no sampling is performed"
    )
    monkeypatch.setattr(cpcv, "_MAX_CELL_EVALUATIONS", 72)
    with pytest.raises(AssertionError, match="no Sharpe may be computed"):
        run(MAIN)  # inclusive limit: 72 passes the check


# ================================================================ rf, context, purity


def test_risk_free_rate_is_applied_to_training_and_path_sharpes():
    rf = Decimal("0.005")
    result = run(MAIN, risk_free_per_period=rf)
    for split in result.splits:  # the selection itself may change with rf, as in PBO
        for column, candidate in enumerate(MAIN):
            expected = reference_sharpe([MAIN[candidate][r] * U for r in split.train_groups], rf)
            assert close(split.train_sharpe_ratios[column], expected)
    for path in result.paths:
        assert close(path.sharpe_ratio, reference_sharpe(path.returns, rf))
        for row, group in enumerate(result.row_groups):
            chosen = result.splits[path.split_indices[group]].selected_candidate_ids
            values = [Decimal(MAIN[c][row]) * U for c in chosen]
            assert path.returns[row] == sum(values) / len(values)


def test_ambient_decimal_context_does_not_change_the_result():
    base = run(TIES)
    with localcontext(Context(prec=3)):
        assert run(TIES) == base


def test_deterministic_and_inputs_untouched():
    m = matrix(TIES)
    model = build_combinatorial_fold_model(groups(4), test_group_count=2)
    returns = m.returns
    first = compute_cpcv_paths(m, model)
    assert compute_cpcv_paths(m, model) == first
    assert m.returns is returns
    assert first.candidate_ids is m.candidate_ids


def test_result_models_validate_their_fields():
    with pytest.raises(ValueError, match="^selected_candidate_ids must not be empty$"):
        CpcvSplitResult(0, (0,), (1,), 2, (Decimal(1),), ())
    with pytest.raises(ValueError, match="^sharpe_ratio must be a finite Decimal"):
        CpcvPathResult(0, (0,), (U,), (), Decimal("Infinity"))
    with pytest.raises(TypeError, match=r"^splits\[0\] must be a CpcvSplitResult$"):
        CpcvResult(("A",), (0,), ("x",), ())


# ================================================================ real rolling integration


def test_real_rolling_trial_group_matrix_cpcv_integration(tmp_path):
    store = pbo_tests.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([pbo_tests._candle(h, p) for h, p in enumerate(pbo_tests.PRICES)])
        windows = (
            TemporalWindow(start=pbo_tests.START, end=pbo_tests.START + H * 6),
            TemporalWindow(start=pbo_tests.START + H * 8, end=pbo_tests.START + H * 14),
        )
        real = build_trial_return_matrix(
            pbo_tests._rolling_group(store, windows, pbo_tests.POLICIES)
        )
        start = pbo_tests.START
        fold_groups = (  # contiguous span; the second rolling window starts after a gap
            TemporalWindow(start=start, end=start + H * 3),
            TemporalWindow(start=start + H * 3, end=start + H * 6),
            TemporalWindow(start=start + H * 6, end=start + H * 11),
            TemporalWindow(start=start + H * 11, end=start + H * 14),
        )
        model = build_combinatorial_fold_model(fold_groups, test_group_count=2)
        result = compute_cpcv_paths(real, model)
        assert result.row_groups == (0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3)
        assert len(result.paths) == 3 and all(len(p.returns) == 12 for p in result.paths)
        for path in result.paths:
            for row, group in enumerate(result.row_groups):
                split = result.splits[path.split_indices[group]]
                columns = [real.candidate_ids.index(c) for c in split.selected_candidate_ids]
                values = [real.returns[row][c] for c in columns]
                assert path.returns[row] == (values[0] if len(values) == 1 else
                                             sum(values) / len(values))  # fmt: skip
        assert compute_cpcv_paths(real, model) == result
    finally:
        store.close()


# ================================================================ path distribution summary (§17.2.20-17.2.24)


def sqrt50(fraction):
    with localcontext(Context(prec=50)):
        return (Decimal(fraction.numerator) / Decimal(fraction.denominator)).sqrt()


def reference_distribution(values):
    """Independent 50-digit mean and sample standard deviation of Sharpe ratios."""
    with localcontext(Context(prec=50)):
        mean = sum(values, Decimal(0)) / len(values)
        stdev = (sum((v - mean) ** 2 for v in values) / (len(values) - 1)).sqrt()
    return mean, stdev


def test_summary_of_the_hand_derived_fixture():
    summary = summarize_cpcv_paths(run(MAIN))
    # p0 (1,2,1,6): mean 5/2, variance 17/3 -> (5/2) sqrt(3/17) ~ 1.050210
    # p1 (2,1,2,6): mean 11/4, variance 59/12 -> (11/4) sqrt(12/59) ~ 1.240216
    # p2 (3,1,5,3): mean 3, variance 8/3 -> (3/4) sqrt(6) ~ 1.837117
    s0 = Decimal(5) / 2 * sqrt50(Fraction(3, 17))
    s1 = Decimal(11) / 4 * sqrt50(Fraction(12, 59))
    s2 = Decimal(3) / 4 * sqrt50(Fraction(6))
    assert (summary.path_count, summary.observations_per_path) == (3, 4)
    assert all(close(a, b) for a, b in zip(summary.path_sharpe_ratios, (s0, s1, s2), strict=True))
    assert summary.sorted_path_indices == (0, 1, 2)
    assert summary.minimum_sharpe_ratio == summary.path_sharpe_ratios[0]
    assert summary.maximum_sharpe_ratio == summary.path_sharpe_ratios[2]
    assert summary.median_sharpe_ratio == summary.path_sharpe_ratios[1]  # odd count: middle
    mean, stdev = reference_distribution([s0, s1, s2])  # ~1.375848, ~0.410613
    assert close(summary.mean_sharpe_ratio, mean)
    assert close(summary.sample_stdev_sharpe_ratio, stdev)
    assert (summary.tie_averaged_path_count, summary.rows_identical_on_all_paths) == (0, 0)
    assert summary.paths_share_observations is True
    assert summary.label_horizon_purging_applied is False
    for phrase in ("offline research diagnostic", "NOT independent samples",
                   "no label/outcome-horizon purging", "no threshold, p-value or pass/fail",
                   "not a full CPCV validation", "not a live strategy approval"):  # fmt: skip
        assert phrase in summary.diagnostic_scope


def test_summary_orders_paths_by_sharpe_and_counts_ties():
    summary = summarize_cpcv_paths(run(TIES))
    # p0 (1,2,5,7): 15/4 / sqrt(91/12) ~ 1.3617; p1 (1,1,2,7): 11/4 / sqrt(33/4) ~ 0.9574;
    # p2 (3,1,5,3): (3/4) sqrt(6) ~ 1.8371 -> ascending p1, p0, p2; median p0
    s0 = Decimal(15) / 4 / sqrt50(Fraction(91, 12))
    s1 = Decimal(11) / 4 / sqrt50(Fraction(33, 4))
    assert close(summary.path_sharpe_ratios[0], s0) and close(summary.path_sharpe_ratios[1], s1)
    assert summary.sorted_path_indices == (1, 0, 2)
    assert summary.median_sharpe_ratio == summary.path_sharpe_ratios[0]
    assert summary.tie_averaged_path_count == 3  # every path uses a tied split


def test_equal_path_sharpes_keep_path_order_and_zero_dispersion():
    # A dominates every two-row training set (mean/|diff| >= 3.8 vs B <= 1.75): every split
    # selects A, so the three paths are A's own returns and share every row
    summary = summarize_cpcv_paths(run({"A": (10, 11, 12, 13), "B": (1, 5, 2, 9)}))
    assert len(set(summary.path_sharpe_ratios)) == 1
    assert summary.sorted_path_indices == (0, 1, 2)
    assert (
        summary.minimum_sharpe_ratio == summary.maximum_sharpe_ratio == summary.median_sharpe_ratio
    )
    assert summary.mean_sharpe_ratio == summary.path_sharpe_ratios[0]
    assert summary.sample_stdev_sharpe_ratio == Decimal(0)
    assert summary.rows_identical_on_all_paths == 4


def test_even_path_count_median_is_the_mean_of_the_two_middle_values():
    columns = {"A": (1, 2, 5, 3, 4), "B": (3, 1, 2, 6, 5), "C": (2, 5, 1, 7, 3)}
    result = run(columns, n=5, k=2)
    summary = summarize_cpcv_paths(result)
    assert summary.path_count == 4  # C(4, 1)
    reference = sorted(reference_sharpe(p.returns) for p in result.paths)
    with localcontext(Context(prec=50)):
        expected_median = (reference[1] + reference[2]) / 2
    assert close(summary.median_sharpe_ratio, expected_median)
    assert [summary.path_sharpe_ratios[i] for i in summary.sorted_path_indices] == sorted(
        summary.path_sharpe_ratios
    )


def test_a_single_path_is_not_a_distribution():
    result = run(MAIN, k=1)
    with pytest.raises(ValueError) as caught:
        summarize_cpcv_paths(result)
    assert str(caught.value) == (
        "a path distribution needs at least 2 paths, got 1 (test_group_count = 1 is plain "
        "K-fold with a single path)"
    )


def test_invalid_summary_inputs_have_exact_messages():
    with pytest.raises(TypeError, match="^result must be a CpcvResult, got dict$"):
        summarize_cpcv_paths({})
    base = run(MAIN)
    shuffled = CpcvResult(base.candidate_ids, base.row_groups, base.splits,
                          (base.paths[1], base.paths[0], base.paths[2]))  # fmt: skip
    with pytest.raises(ValueError, match=r"^paths must be ordered by path_index 0\.\.P-1$"):
        summarize_cpcv_paths(shuffled)
    short = CpcvPathResult(0, base.paths[0].split_indices, base.paths[0].returns[:3], (),
                           base.paths[0].sharpe_ratio)  # fmt: skip
    broken = CpcvResult(base.candidate_ids, base.row_groups, base.splits,
                        (short, base.paths[1], base.paths[2]))  # fmt: skip
    with pytest.raises(
        ValueError, match=r"^path 0 has 3 returns, expected 4 \(one per matrix row\)$"
    ):
        summarize_cpcv_paths(broken)


def test_summary_is_context_independent_deterministic_and_pure():
    result = run(TIES)
    paths = result.paths
    base = summarize_cpcv_paths(result)
    with localcontext(Context(prec=3)):
        assert summarize_cpcv_paths(run(TIES)) == base
    assert summarize_cpcv_paths(result) == base
    assert result.paths is paths


def test_summary_model_refuses_a_purging_claim_and_non_finite_values():
    base = summarize_cpcv_paths(run(MAIN))
    fields = {name: getattr(base, name) for name in CpcvPathSummary.__slots__}
    with pytest.raises(ValueError, match="^label/outcome-horizon purging is not implemented"):
        CpcvPathSummary(**fields | {"label_horizon_purging_applied": True})
    with pytest.raises(ValueError, match="^mean_sharpe_ratio must be a finite Decimal"):
        CpcvPathSummary(**fields | {"mean_sharpe_ratio": Decimal("NaN")})
