"""PBO side statistics (VALIDATION_SPEC.md Bölüm 17.5.25-17.5.30, 28.V).

Two-row half-samples make every Sharpe ratio sqrt(2) * mean / |difference|,
so selections, losses and the degradation line are derived by hand.
"""

from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest
import test_validation_pbo as base

from crypto_quant_lab.validation.pbo import compute_probability_of_backtest_overfitting
from crypto_quant_lab.validation.pbo_diagnostics import (
    STOCHASTIC_DOMINANCE_STATUS,
    compute_pbo_diagnostics,
)
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix, build_trial_return_matrix
from crypto_quant_lab.validation.windows import TemporalWindow

# rows 0-1 = block 0, rows 2-3 = block 1 (units of 0.01)
A = ("4", "2", "-1", "-2")  # block 0: mean 3 / |2| -> 3/2 ; block 1: -1.5 / 1 -> -3/2
B = ("1", "3", "3", "1")  # block 0: 2 / 2 -> 1 ; block 1: 2 / 2 -> 1
C = ("4", "2", "2", "1")  # block 0 identical to A (IS tie) ; block 1: 1.5 / 1 -> 3/2


def matrix(**columns):
    ids = tuple(columns)
    return TrialReturnMatrix(
        candidate_ids=ids,
        observation_times=tuple(base.START + timedelta(hours=t + 1) for t in range(4)),
        window_indices=(0, 0, 0, 0),
        returns=tuple(tuple(Decimal(columns[c][t]) / 100 for c in ids) for t in range(4)),
    )


def test_hand_case_degradation_line_and_probability_of_loss():
    d = compute_pbo_diagnostics(matrix(A=A, B=B), block_count=2)
    # comb 0 (IS block 0): A 3/2 > B 1 -> A, OOS -3/2 < 0 (loss)
    # comb 1 (IS block 1): B 1 > A -3/2 -> B, OOS 1 >= 0
    assert (d.combination_count, d.probability_of_loss) == (2, Decimal("0.5"))
    root2 = Decimal(2).sqrt()
    expected_points = [(Decimal("1.5") * root2, Decimal("-1.5") * root2), (root2, root2)]
    for (x, y, w), (ex, ey) in zip(d.points, expected_points, strict=True):
        assert abs(x - ex) <= Decimal("1e-26") and abs(y - ey) <= Decimal("1e-26") and w == 1
    # slope = (1 - (-3/2)) / (1 - 3/2) = -5 ; intercept = sqrt(2) * (1 + 5) = 6 sqrt(2)
    assert abs(d.degradation_slope - Decimal(-5)) <= Decimal("1e-25")
    with localcontext(Context(prec=50)):
        assert abs(d.degradation_intercept - 6 * Decimal(2).sqrt()) <= Decimal("1e-25")
    assert d.stochastic_dominance == STOCHASTIC_DOMINANCE_STATUS
    assert d.stochastic_dominance.startswith("not_evaluated")


def test_tied_in_sample_winners_share_the_combination_weight():
    d = compute_pbo_diagnostics(matrix(A=A, B=B, C=C), block_count=2)
    # comb 0: A and C tie at 3/2 -> weight 1/2 each; A's OOS -3/2 is a loss, C's 3/2 is not
    # comb 1: C 3/2 > B 1 > A -3/2 -> C, OOS 3/2 -> no loss ; P(loss) = (1/2) / 2 = 1/4
    assert d.probability_of_loss == Decimal("0.25")
    assert [w for _, _, w in d.points] == [Decimal("0.5"), Decimal("0.5"), Decimal(1)]


def test_constant_in_sample_sharpe_leaves_the_slope_undefined():
    same = ("1", "3", "1", "3")  # both halves 2 / 2 -> 1
    other = ("1", "2", "1", "2")  # both halves 1.5 / 1 -> 3/2
    d = compute_pbo_diagnostics(matrix(S=same, O=other), block_count=2)
    assert (d.degradation_slope, d.degradation_intercept) == (None, None)
    assert d.probability_of_loss == 0


def test_validation_is_the_pbo_validation():
    with pytest.raises(ValueError, match="block_count must be an even integer >= 2"):
        compute_pbo_diagnostics(matrix(A=A, B=B), block_count=3)
    with pytest.raises(TypeError, match="matrix must be a TrialReturnMatrix"):
        compute_pbo_diagnostics("m", block_count=2)


def test_real_rolling_group_matches_the_pbo_selections(tmp_path):
    store = base.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        windows = (TemporalWindow(start=base.START, end=base.START + base.HOUR * 6),
                   TemporalWindow(start=base.START + base.HOUR * 8,
                                  end=base.START + base.HOUR * 14))  # fmt: skip
        real = build_trial_return_matrix(base._rolling_group(store, windows, base.POLICIES))
        d = compute_pbo_diagnostics(real, block_count=4)
        pbo = compute_probability_of_backtest_overfitting(real, block_count=4)
        assert d.combination_count == len(pbo.combinations) == 6
        assert len(d.points) == sum(len(c.selected_candidate_ids) for c in pbo.combinations)
        assert Decimal(0) <= d.probability_of_loss <= Decimal(1)
        assert compute_pbo_diagnostics(real, block_count=4) == d
    finally:
        store.close()
