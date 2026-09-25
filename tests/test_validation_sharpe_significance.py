"""One-sided PSR p-values + Holm family (VALIDATION_SPEC.md Bölüm 17.6.11-17.6.18, 28.S).

Hand cases: returns alternating 0.03 / -0.01 (T = 4) have mean 0.01, sample
stdev 0.02*sqrt(4/3), so SR = sqrt(3)/4; population skew 0 and kurtosis 1 make
the variance term 1 - 0 + 0 = 1, hence z = SR*sqrt(3) = 3/4 and
p = 1 - Phi(0.75). Symmetric returns with mean 0 give SR = 0, z = 0, p = 1/2.
Phi is checked against math.erf (independent of the module under test).
"""

import inspect
import math
from datetime import timedelta
from decimal import Context, Decimal, localcontext

import pytest
import test_validation_deflated_sharpe as dsr_tests

from crypto_quant_lab.validation import sharpe_significance as module
from crypto_quant_lab.validation.rolling import WindowResult
from crypto_quant_lab.validation.sharpe_significance import (
    ASSUMPTION,
    FAMILY_SCOPE,
    SharpeSignificance,
    compute_sharpe_significance,
    evaluate_sharpe_family,
)
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow

# equity 1000 -> 1030 -> 1019.7 -> 1050.291 -> 1039.78809: returns 0.03, -0.01, 0.03, -0.01
ALTERNATING = ["1030", "1019.7", "1050.291", "1039.78809"]
# equity 1000 -> 1020 -> 999.6 -> 1019.592 -> 999.20016: returns +0.02, -0.02, +0.02, -0.02
SYMMETRIC = ["1020", "999.6", "1019.592", "999.20016"]
PHI_075 = 0.5 * (1 + math.erf(0.75 / math.sqrt(2)))  # independent float reference


def group(curves, *, windows_per_trial=1):
    trials = []
    for candidate_id, curve in curves.items():
        result = dsr_tests._result(curve)
        results = tuple(
            WindowResult(window=TemporalWindow(start=dsr_tests.START + timedelta(hours=8 * i),
                                               end=dsr_tests.START + timedelta(hours=8 * i + 8)),
                         result=result)
            for i in range(windows_per_trial)
        )  # fmt: skip
        trials.append(dsr_tests._trial(candidate_id, results))
    return TrialGroup(group_id="family-1", trials=tuple(trials))


def test_hand_case_z_three_quarters():
    (result,) = compute_sharpe_significance(group({"alpha": ALTERNATING}))
    assert result.sample_length == 4
    assert abs(result.sharpe_ratio - Decimal(3).sqrt() / 4) <= Decimal("1e-26")
    assert abs(result.skewness) <= Decimal("1e-26")
    assert abs(result.kurtosis - 1) <= Decimal("1e-26")
    assert abs(result.z_statistic - Decimal("0.75")) <= Decimal("1e-26")
    assert abs(float(result.p_value) - (1 - PHI_075)) < 1e-15  # ~0.2266273524
    # lag-1 autocorrelation of +d, -d, +d, -d around the mean: 3 * (-d^2) / (4 d^2) = -3/4
    assert result.lag1_autocorrelation == Decimal("-0.75")


def test_zero_mean_gives_p_one_half():
    (result,) = compute_sharpe_significance(group({"flat": SYMMETRIC}))
    assert result.sharpe_ratio == 0 and result.z_statistic == 0
    assert result.p_value == Decimal("0.5")


def test_holm_family_over_the_recorded_trials():
    family = evaluate_sharpe_family(
        group({"alpha": ALTERNATING, "flat": SYMMETRIC}), significance_level=Decimal("0.05")
    )
    alpha, flat = family.results
    assert family.family_id == "family-1"
    assert [h.hypothesis_id for h in family.holm.hypotheses] == ["alpha", "flat"]
    # Holm, m = 2: sorted p = (0.2266.., 0.5): adjusted 2 * 0.2266.. = 0.4532..,
    # then max(0.4532.., 1 * 0.5) = 0.5; neither is <= 0.05
    assert family.holm.hypotheses[1].adjusted_p_value == Decimal("0.5")
    assert family.holm.hypotheses[0].adjusted_p_value == 2 * alpha.p_value
    assert [h.rejected for h in family.holm.hypotheses] == [False, False]
    assert family.holm.hypotheses[0].p_value is alpha.p_value and flat.p_value == Decimal("0.5")
    assert "i.i.d." in family.assumption and family.assumption == ASSUMPTION
    assert "lower bound" in family.family_scope and family.family_scope == FAMILY_SCOPE
    assert "one-sided" in family.null_hypothesis


def test_risk_free_rate_moves_the_null():
    (base,) = compute_sharpe_significance(group({"alpha": ALTERNATING}))
    (shifted,) = compute_sharpe_significance(
        group({"alpha": ALTERNATING}), risk_free_per_period=Decimal("0.01")
    )
    assert shifted.sharpe_ratio == 0 and shifted.p_value == Decimal("0.5")  # excess mean 0
    assert base.p_value < shifted.p_value


def test_multi_window_trials_are_rejected_before_any_computation(monkeypatch):
    monkeypatch.setattr(module, "_significance", lambda *a: pytest.fail("computed"))
    with pytest.raises(ValueError) as caught:
        compute_sharpe_significance(group({"alpha": ALTERNATING}, windows_per_trial=2))
    assert str(caught.value) == (
        "trials[0] must have exactly one window result for a Sharpe p-value, got 2 "
        "(multi-window pooling is not defined)"
    )


def test_invalid_arguments_and_undefined_statistics():
    with pytest.raises(TypeError, match="^group must be a TrialGroup, got str$"):
        compute_sharpe_significance("g")
    with pytest.raises(TypeError, match="^risk_free_per_period must be a Decimal, got int$"):
        compute_sharpe_significance(group({"a": ALTERNATING}), risk_free_per_period=0)
    with pytest.raises(ValueError, match="^risk_free_per_period must be finite, got NaN$"):
        compute_sharpe_significance(group({"a": ALTERNATING}), risk_free_per_period=Decimal("NaN"))
    # constant returns: Stage-2's own undefined-Sharpe error propagates unchanged
    with pytest.raises(ValueError, match="return_stdev must be greater than zero"):
        compute_sharpe_significance(group({"a": ["1010", "1020.1", "1030.301", "1040.60401"]}))
    with pytest.raises(ValueError, match="significance_level"):
        evaluate_sharpe_family(group({"a": ALTERNATING}), significance_level=Decimal(0))


def test_model_and_context_independence():
    with pytest.raises(ValueError, match=r"^p_value must be a Decimal within \[0, 1\]"):
        SharpeSignificance("a", 4, Decimal(0), Decimal(0), Decimal(1), Decimal(0),
                           Decimal("1.5"), None)  # fmt: skip
    g = group({"alpha": ALTERNATING, "flat": SYMMETRIC})
    base = evaluate_sharpe_family(g, significance_level=Decimal("0.05"))
    with localcontext(Context(prec=3)):
        assert evaluate_sharpe_family(g, significance_level=Decimal("0.05")) == base


def test_reuses_the_locked_dsr_normal_cdf_and_selects_nothing():
    from crypto_quant_lab.validation import deflated_sharpe

    assert module._normal_cdf is deflated_sharpe._normal_cdf
    names = {name.lower() for name in dir(module)}
    assert not {"select", "rank", "best", "approve"} & names
    source = inspect.getsource(module)
    assert "float(" not in source and "import math" not in source


def test_real_rolling_group_family(tmp_path):
    import test_validation_pbo as base

    store = base.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        window = (TemporalWindow(start=base.START, end=base.START + base.HOUR * 16),)
        real = base._rolling_group(store, window, base.POLICIES)
        family = evaluate_sharpe_family(real, significance_level=Decimal("0.05"))
        assert [r.candidate_id for r in family.results] == ["long", "short", "alternating"]
        assert all(r.sample_length == 16 for r in family.results)
        for r in family.results:  # z and p recomputed from the reported statistics
            with localcontext(Context(prec=50)):
                term = 1 - r.skewness * r.sharpe_ratio + (r.kurtosis - 1) / 4 * r.sharpe_ratio**2
                z = r.sharpe_ratio * Decimal(15).sqrt() / term.sqrt()
            assert abs(z - r.z_statistic) <= Decimal("1e-24")
            phi = 0.5 * (1 + math.erf(float(r.z_statistic) / math.sqrt(2)))
            assert abs(float(r.p_value) - (1 - phi)) < 1e-12
        adjusted = [h.adjusted_p_value for h in family.holm.hypotheses]
        assert all(a >= r.p_value for a, r in zip(adjusted, family.results, strict=True))
        assert evaluate_sharpe_family(real, significance_level=Decimal("0.05")) == family
    finally:
        store.close()
