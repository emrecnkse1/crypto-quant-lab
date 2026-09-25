"""Effective N and pooled (multi-window) DSR (VALIDATION_SPEC.md Bölüm 17.4.18-17.4.24, 28.U).

Effective-N expectations are hand-derived correlations; the pooled DSR is
checked against the locked single-window DSR on equivalent evidence.
"""

from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest
import test_validation_deflated_sharpe as dsr_tests

from crypto_quant_lab.validation.deflated_sharpe import compute_deflated_sharpe_ratio
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix
from crypto_quant_lab.validation.rolling import WindowResult
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.trial_statistics import (
    compute_pooled_deflated_sharpe_ratio,
    estimate_effective_trial_count,
)
from crypto_quant_lab.validation.windows import TemporalWindow

X = ("1", "-1", "1", "-1")
Y = ("1", "1", "-1", "-1")  # orthogonal to X after centering: corr(X, Y) = 0


def matrix(**columns):
    ids = tuple(columns)
    rows = len(next(iter(columns.values())))
    return TrialReturnMatrix(
        candidate_ids=ids,
        observation_times=tuple(dsr_tests.START + timedelta(hours=t + 1) for t in range(rows)),
        window_indices=(0,) * rows,
        returns=tuple(tuple(Decimal(columns[c][t]) / 100 for c in ids) for t in range(rows)),
    )


def test_effective_n_hand_cases():
    zero = estimate_effective_trial_count(matrix(x=X, y=Y))
    assert (zero.mean_correlation, zero.effective_trial_count) == (0, 2)  # N = 0 + 1 * 2
    same = estimate_effective_trial_count(matrix(x=X, y=X))
    assert (same.mean_correlation, same.effective_trial_count) == (1, 1)  # N = 1 + 0 * 2
    opposite = estimate_effective_trial_count(matrix(x=X, y=tuple(str(-int(v)) for v in X)))
    assert (opposite.mean_correlation, opposite.effective_trial_count) == (-1, 3)
    assert opposite.exceeds_trial_count is True  # reported, never clamped
    # z = x + y: corr(x, z) = corr(y, z) = 1/sqrt(2); rho = sqrt(2)/3; N = 3 - 2 sqrt(2)/3
    z = tuple(str(int(a) + int(b)) for a, b in zip(X, Y, strict=True))
    three = estimate_effective_trial_count(matrix(x=X, y=Y, z=z))
    with localcontext(Context(prec=50)):
        expected = 3 - 2 * Decimal(2).sqrt() / 3
    assert abs(three.effective_trial_count - expected) <= Decimal("1e-26")
    assert (three.trial_count, three.pair_count) == (3, 3)


def test_effective_n_invalid_inputs():
    with pytest.raises(ValueError, match="candidate 'c' has zero return variance"):
        estimate_effective_trial_count(matrix(x=X, c=("2", "2", "2", "2")))
    with pytest.raises(ValueError, match="at least two trials are required, got 1"):
        estimate_effective_trial_count(matrix(x=X))
    with pytest.raises(TypeError, match="matrix must be a TrialReturnMatrix"):
        estimate_effective_trial_count("m")


def test_pooled_dsr_equals_the_locked_dsr_for_single_window_trials():
    group = dsr_tests._group()
    for n in (2, 5, 50):
        assert compute_pooled_deflated_sharpe_ratio(
            group, selected_candidate_id="alpha", independent_trial_count=n
        ) == compute_deflated_sharpe_ratio(
            group, selected_candidate_id="alpha", independent_trial_count=n
        )


def _two_window_group(first, second):
    w1 = TemporalWindow(start=dsr_tests.START, end=dsr_tests.START + timedelta(hours=8))
    w2 = TemporalWindow(start=w1.end, end=w1.end + timedelta(hours=8))
    trials = tuple(
        dsr_tests._trial(cid, (WindowResult(window=w1, result=dsr_tests._result(first[cid])),
                               WindowResult(window=w2, result=dsr_tests._result(second[cid]))))
        for cid in first
    )  # fmt: skip
    return TrialGroup(group_id="pooled", trials=trials)


def _continued(first_curve, second_curve):
    """One single-window curve whose periodic returns are first's then second's."""
    with localcontext(Context(prec=80)):
        equity, out = Decimal(1000), []
        for curve in (first_curve, second_curve):
            previous = Decimal(1000)
            for point in curve:
                equity = equity * Decimal(point) / previous
                previous = Decimal(point)
                out.append(str(equity))
    return tuple(out)


def test_pooled_dsr_matches_dsr_on_the_equivalent_single_window_evidence():
    first = dsr_tests.SYNTHETIC_CURVES
    second = {cid: tuple(reversed(curve)) for cid, curve in first.items()}
    pooled = compute_pooled_deflated_sharpe_ratio(
        _two_window_group(first, second), selected_candidate_id="beta", independent_trial_count=7
    )
    long_curves = {cid: _continued(first[cid], second[cid]) for cid in first}
    window = TemporalWindow(start=dsr_tests.START, end=dsr_tests.START + timedelta(hours=16))
    reference = compute_deflated_sharpe_ratio(
        dsr_tests._group(long_curves, window=window),
        selected_candidate_id="beta",
        independent_trial_count=7,
    )
    assert abs(pooled - reference) <= Decimal("1e-20")


def test_pooled_dsr_invalid_inputs():
    group = dsr_tests._group()
    with pytest.raises(ValueError, match="'nope' is not in the group"):
        compute_pooled_deflated_sharpe_ratio(group, selected_candidate_id="nope",
                                             independent_trial_count=2)  # fmt: skip
    with pytest.raises(ValueError, match="between 2 and 10\\*\\*30"):
        compute_pooled_deflated_sharpe_ratio(group, selected_candidate_id="alpha",
                                             independent_trial_count=1)  # fmt: skip
    with pytest.raises(TypeError, match="group must be a TrialGroup"):
        compute_pooled_deflated_sharpe_ratio("g", selected_candidate_id="a",
                                             independent_trial_count=2)  # fmt: skip
