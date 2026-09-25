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
    compute_hac_sharpe_significance,
    compute_sharpe_significance,
    evaluate_hac_sharpe_family,
    evaluate_sharpe_family,
    newey_west_default_lag,
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


# ================================================================ HAC (Lo 2002 + Newey-West), Bölüm 17.6.19-17.6.24

# returns +0.03, -0.01 repeated (T = 6): mean 0.01, population variance 0.0004, SR_pop = 1/2,
# deviations +-0.02 so (r - mu)^2 - sigma^2 = 0 and only Gamma_11 matters
SIX = ["1030", "1019.7", "1050.291", "1039.78809", "1070.9817327", "1060.271915373"]


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def test_hac_lag_zero_reduces_to_the_iid_variance_term():
    (result,) = compute_hac_sharpe_significance(group({"a": SIX}), lag=0)
    # V = 1 - skew*SR + (kurt - 1)/4 * SR^2 = 1 (skew 0, kurt 1); z = SR sqrt(T) = sqrt(6)/2
    assert result.status == "evaluated" and result.lag == 0
    assert abs(result.variance - 1) <= Decimal("1e-26")
    assert abs(result.sharpe_ratio - Decimal("0.5")) <= Decimal("1e-26")
    assert abs(float(result.p_value) - (1 - phi(math.sqrt(6) / 2))) < 1e-15


def test_hac_lag_one_hand_case_negative_autocorrelation_shrinks_the_variance():
    (result,) = compute_hac_sharpe_significance(group({"a": SIX}), lag=1)
    # Gamma_1 = 5 * (-0.0004) / 6; Sigma_11 = 0.0004 + 2 * (1/2) * Gamma_1 = 0.0004 / 6
    # V = Sigma_11 / sigma^2 = 1/6; z = (1/2) * sqrt(6) / sqrt(1/6) = 3
    assert abs(result.variance - Decimal(1) / 6) <= Decimal("1e-26")
    assert abs(result.z_statistic - 3) <= Decimal("1e-25")
    assert abs(float(result.p_value) - (1 - phi(3.0))) < 1e-15


def test_hac_asymmetric_series_matches_an_independent_fraction_computation():
    from fractions import Fraction

    curve = ["1010", "1040.3", "1029.897", "1060.79391", "1071.4018491", "1060.687830609"]
    returns = [Fraction(1, 100), Fraction(3, 100), Fraction(-1, 100), Fraction(3, 100),
               Fraction(1, 100), Fraction(-1, 100)]  # fmt: skip
    (result,) = compute_hac_sharpe_significance(group({"a": curve}), lag=1)
    n = len(returns)
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / n
    h = [(r - mu, (r - mu) ** 2 - var) for r in returns]

    def gamma(j):
        return [[sum(h[t][a] * h[t - j][b] for t in range(j, n)) / n for b in (0, 1)]
                for a in (0, 1)]  # fmt: skip

    s = gamma(0)
    for j in (1,):
        w, g = 1 - Fraction(j, 2), gamma(j)
        s = [[s[a][b] + w * (g[a][b] + g[b][a]) for b in (0, 1)] for a in (0, 1)]
    with localcontext(Context(prec=60)):
        sigma = (Decimal(var.numerator) / Decimal(var.denominator)).sqrt()
        sr = (Decimal(mu.numerator) / Decimal(mu.denominator)) / sigma
        d1, d2 = 1 / sigma, -sr / (2 * Decimal(var.numerator) / Decimal(var.denominator))

        def dec(f):
            return Decimal(f.numerator) / Decimal(f.denominator)

        expected = (d1 * d1 * dec(s[0][0]) + d1 * d2 * (dec(s[0][1]) + dec(s[1][0]))
                    + d2 * d2 * dec(s[1][1]))  # fmt: skip
    assert abs(result.variance - expected) <= Decimal("1e-24")


def test_hac_pools_windows_without_crossing_their_boundary():
    (result,) = compute_hac_sharpe_significance(group({"a": SIX}, windows_per_trial=2), lag=1)
    # T = 12, ten within-window lag-1 pairs: Gamma_1 = 10 * (-0.0004) / 12 -> V = 1/6
    # (including the boundary pair would give eleven pairs and V = 1/12)
    assert (result.sample_length, result.window_count) == (12, 2)
    assert abs(result.variance - Decimal(1) / 6) <= Decimal("1e-26")


def test_hac_not_evaluated_is_explicit_and_blocks_the_family():
    (short,) = compute_hac_sharpe_significance(group({"a": ALTERNATING}))  # T = 4, lag 1
    assert (short.status, short.p_value, short.lag) == ("not_evaluated", None, 1)
    assert short.reason.startswith("T = 4 is not larger than 2 (lag + 1) = 4")
    constant = ["1010", "1020.1", "1030.301", "1040.60401", "1051.0100501", "1061.520150601"]
    (flat,) = compute_hac_sharpe_significance(group({"c": constant}), lag=0)
    assert flat.status == "not_evaluated" and "not positive" in flat.reason
    family = evaluate_hac_sharpe_family(group({"a": SIX, "b": ALTERNATING}),
                                        significance_level=Decimal("0.05"))  # fmt: skip
    assert (family.status, family.holm) == ("not_evaluated", None)
    assert "partial family would understate" in family.reason
    evaluated = evaluate_hac_sharpe_family(group({"a": SIX}), significance_level=Decimal("0.05"),
                                           lag=1)  # fmt: skip
    assert evaluated.status == "evaluated" and evaluated.holm.hypotheses[0].rejected is True
    assert "small-sample accuracy is not guaranteed" in evaluated.assumption


def test_hac_argument_validation_and_default_lag_rule():
    with pytest.raises(ValueError, match="lag must be None or an integer >= 0"):
        compute_hac_sharpe_significance(group({"a": SIX}), lag=-1)
    with pytest.raises(ValueError, match="lag must be None or an integer >= 0"):
        compute_hac_sharpe_significance(group({"a": SIX}), lag=True)
    with pytest.raises(TypeError, match="group must be a TrialGroup"):
        compute_hac_sharpe_significance("g")
    # floor(4 (T/100)^(2/9)): 4*0.04^(2/9) = 1.96 -> 1; T=100 -> 4; 4*10^(2/9) = 6.67 -> 6
    assert [newey_west_default_lag(t) for t in (4, 100, 1000)] == [1, 4, 6]


def test_hac_real_rolling_group_is_internally_consistent(tmp_path):
    import test_validation_pbo as base

    store = base.SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        windows = (TemporalWindow(start=base.START, end=base.START + base.HOUR * 8),
                   TemporalWindow(start=base.START + base.HOUR * 8,
                                  end=base.START + base.HOUR * 16))  # fmt: skip
        real = base._rolling_group(store, windows, base.POLICIES)
        results = compute_hac_sharpe_significance(real)
        for r in results:
            assert (r.sample_length, r.window_count) == (16, 2)
            if r.status == "evaluated":
                with localcontext(Context(prec=50)):
                    z = r.sharpe_ratio * Decimal(16).sqrt() / r.variance.sqrt()
                assert abs(z - r.z_statistic) <= Decimal("1e-24")
        assert compute_hac_sharpe_significance(real) == results
    finally:
        store.close()
