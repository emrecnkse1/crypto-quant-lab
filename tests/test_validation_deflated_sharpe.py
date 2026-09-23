import decimal
import inspect
import itertools
import math
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.annualized_metrics as annualized_metrics_module
import crypto_quant_lab.validation.candidate as candidate_module
import crypto_quant_lab.validation.deflated_sharpe as dsr_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.purging as purging_module
import crypto_quant_lab.validation.rolling as rolling_module
import crypto_quant_lab.validation.trial_group as trial_group_module
import crypto_quant_lab.validation.windows as windows_module
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.deflated_sharpe import compute_deflated_sharpe_ratio
from crypto_quant_lab.validation.metrics import compute_stage2_metrics
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow

# ================================================================
# Independent high-precision references (VALIDATION_SPEC.md Bölüm 17.4.12)
#
# Generated OFFLINE (not at test time) with mpmath 1.3.0 at mp.dps=130 — an
# arbitrary-precision library whose normal CDF is erfc-based and whose
# quantile is sqrt(2)*erfinv(2p-1): algorithms independent of the Taylor
# series / Newton iteration under test. mpmath is not a project dependency;
# the values are stored here as Decimal strings. CDF values carry 90
# significant digits, quantiles 70, DSR values 40.
# ================================================================

CDF_REFERENCES = (
    ("0", "0.5"),
    ("0.5", "0.691462461274013103637704610608337739883602175554577936820776142679155795406279544025241060"),
    ("1", "0.841344746068542948585232545632037922477912966726604390987394450242991441987204829500884918"),
    ("-1", "0.158655253931457051414767454367962077522087033273395609012605549757008558012795170499115082"),
    ("2.5", "0.993790334674223864833021895425807778872102253076907231731437145296669763592247503675116588"),
    ("-3", "0.00134989803163009452665181476759497737782936815838064936422198535580572076457210026803858129"),
    ("5", "0.999999713348428120806088326247667125354646145576986388110426914507201065241230077936006636"),
    ("-7.5", "3.19089167291089622776728834472635531287563678435469419353568198586410612060691686481408205E-14"),
    ("10", "0.999999999999999999999992380146975839473934026656748400691636495966722043039421964644537103"),
    ("-10", "7.61985302416052606597334325159930836350403327795696057803535546289661562205964817033415139E-24"),
    ("-12", "1.77648211207767899769617100184555709239266643417895318503866117334944436838001444942033055E-33"),
    ("14.5", "0.999999999999999999999999999999999999999999999993942505235584779220366550214487127249656800"),
    ("-14.5", "6.05749476441522077963344978551287275034320038395367719312842902911156092733868178115030865E-48"),
)  # fmt: skip

# p values for the DSR domain are the exact Decimal values the module builds
# for N in {2, 100, 10**30} (1 - 1/N and 1 - 1/(N e), 80-digit context).
QUANTILE_REFERENCES = (
    ("0.975", "1.959963984540054235524594430520551527955550077869548398476952646361635"),
    ("0.99", "2.326347874040841100885606163346911723351817141532013069065640247890877"),
    ("0.81606027941427883920223811491926956627709443448411608274608159915126925212755010",
     "0.9004525966377903411461515142137625515878635806480198572036337755562722"),
    ("0.99632120558828557678404476229838539132554188868968232165492163198302538504255100",
     "2.680210444966882695961636305408196605716103790357549847239734373844243"),
    ("0.999999999999999999999999999999",
     "11.46402468844361572698226422123603724396129845883419323840941251453226"),
    ("0.99999999999999999999999999999963212055882855767840447622983853913255418886896823",
     "11.55028518429618777414438385229581343129958924930199934603770694039618"),
)  # fmt: skip

# Bailey & López de Prado (2014) numerical example inputs (printed pp. 9-10):
# annualized SR 2.5 and annualized V[SR_n] = 1/2 with 250 observations/year,
# T = 1250. References: mpmath on these exact Decimal inputs.
PAPER_SR = Decimal(
    "0.15811388300841896659994467722163592668597775696626084134287524263962972193196191"
)
PAPER_V = Decimal("0.002")
PAPER_T = 1250
PAPER_REFERENCES = (
    (100, Decimal(-3), Decimal(10), "0.9003968344493909615575982981047946845242"),
    (46, Decimal(-3), Decimal(10), "0.9505017068755785914459333763100931213980"),
    (88, Decimal(0), Decimal(3), "0.9504908166760141889022950874066234139024"),
    (89, Decimal(0), Decimal(3), "0.9498393713384772201167760860187634450090"),
)

# Synthetic group: mpmath DSR computed from the exact Decimal Stage-2 Sharpe
# ratios and periodic returns the existing (unchanged) metrics produce.
SYNTHETIC_CURVES = {
    "alpha": ("1010", "1004", "1021", "1015", "1030", "1026", "1041", "1037"),
    "beta": ("995", "1003", "990", "1001", "997", "1008", "999", "1012"),
    "gamma": ("1002", "1001", "1006", "1003", "1009", "1004", "1012", "1010"),
}
GROUP_REFERENCES = (
    ("0", "alpha", 2, "0.8377340225523421232431891525476576844577"),
    ("0", "alpha", 5, "0.7631680190447679526804801208902943035472"),
    ("0", "alpha", 50, "0.6117246526566250943559591126774169436617"),
    ("0", "gamma", 2, "0.6951291404272494792963431394384421489408"),
    ("0", "gamma", 5, "0.5954490120830115710283942740562887127904"),
    ("0", "gamma", 50, "0.4240860631937574385049461488189218806526"),
    ("0.0005", "alpha", 2, "0.8006861201142884869370294749835767664157"),
    ("0.0005", "alpha", 5, "0.7130561921751846598573875293833565393071"),
    ("0.0005", "alpha", 50, "0.5432208000287331786219626478595923836501"),
    ("0.0005", "gamma", 2, "0.5842379241307608755122534007675280196211"),
    ("0.0005", "gamma", 5, "0.4728086007624598309131782111161627228477"),
    ("0.0005", "gamma", 50, "0.3012744792873634909747576914400929360956"),
)

# Tolerances (Bölüm 17.4.8): the measured worst open-interval CDF error is
# ~7e-79 and the measured worst quantile x-error ~6e-50; the tolerances below
# sit well under the locked accuracy targets and above the measured errors.
CDF_ABS_TOLERANCE = Decimal("1e-75")
QUANTILE_X_TOLERANCE = Decimal("1e-47")
DSR_ABS_TOLERANCE = Decimal("1e-27")

START = datetime(2024, 1, 1, tzinfo=UTC)
WINDOW = TemporalWindow(start=START, end=START + timedelta(hours=8))
EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
AS_OF_TIME = datetime(2024, 1, 3, tzinfo=UTC)


def _abs_diff(value, reference):
    with localcontext(Context(prec=150)):
        return abs(Decimal(value) - Decimal(reference))


def _result(equities, *, spacing=timedelta(hours=1), initial_cash=Decimal(1000)):
    points = tuple(
        EquityPoint(time=START + spacing * index, equity=Decimal(value))
        for index, value in enumerate(equities)
    )
    final = points[-1].equity
    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=final,
        final_equity=final,
        total_realized_pnl=Decimal(0),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(0),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=points,
    )


def _trial(candidate_id, results):
    return Trial(
        candidate=Candidate(candidate_id=candidate_id, parameters=()),
        results=results,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
    )


def _group(curves=None, *, spacing=timedelta(hours=1), window=WINDOW):
    curves = SYNTHETIC_CURVES if curves is None else curves
    trials = tuple(
        _trial(candidate_id, (WindowResult(window=window, result=_result(c, spacing=spacing)),))
        for candidate_id, c in curves.items()
    )
    return TrialGroup(group_id="g", trials=trials)


def _dsr(group=None, **overrides):
    kwargs = {"selected_candidate_id": "alpha", "independent_trial_count": 5}
    kwargs.update(overrides)
    return compute_deflated_sharpe_ratio(_group() if group is None else group, **kwargs)


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


def _float_reference_dsr(curves, selected, n, rf=0.0, *, sample_moments=False, excess=False):
    """Independent float (stdlib) DSR — a different implementation, not an oracle copy."""
    sharpes = {}
    for candidate_id, curve in curves.items():
        equities = [1000.0] + [float(v) for v in curve]
        returns = [equities[i] / equities[i - 1] - 1 for i in range(1, len(equities))]
        sharpes[candidate_id] = (statistics.fmean(returns) - rf) / statistics.stdev(returns)
    equities = [1000.0] + [float(v) for v in curves[selected]]
    returns = [equities[i] / equities[i - 1] - 1 for i in range(1, len(equities))]
    t = len(returns)
    mu = statistics.fmean(returns)
    m2 = sum((r - mu) ** 2 for r in returns) / t
    m3 = sum((r - mu) ** 3 for r in returns) / t
    m4 = sum((r - mu) ** 4 for r in returns) / t
    skew = m3 / m2**1.5
    kurt = m4 / m2**2
    if sample_moments:
        skew = skew * math.sqrt(t * (t - 1)) / (t - 2)
        kurt = ((t + 1) * (kurt - 3) + 6) * (t - 1) / ((t - 2) * (t - 3)) + 3
    if excess:
        kurt -= 3
    variance = statistics.variance(list(sharpes.values()))
    nd = statistics.NormalDist()
    gamma = 0.5772156649015329
    sr0 = math.sqrt(variance) * (
        (1 - gamma) * nd.inv_cdf(1 - 1 / n) + gamma * nd.inv_cdf(1 - 1 / (n * math.e))
    )
    sr = sharpes[selected]
    z = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr * sr)
    return nd.cdf(z)


# ================================================================
# API / scope (criteria 1, 2)
# ================================================================


def test_public_symbol_set_is_exactly_the_locked_api():
    public = {name for name in dir(dsr_module) if not name.startswith("_")}
    assert public == {"compute_deflated_sharpe_ratio"}


def test_signature_is_locked():
    signature = inspect.signature(compute_deflated_sharpe_ratio)
    parameters = signature.parameters
    assert list(parameters) == [
        "group",
        "selected_candidate_id",
        "independent_trial_count",
        "risk_free_per_period",
    ]
    assert parameters["group"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("selected_candidate_id", "independent_trial_count", "risk_free_per_period"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["selected_candidate_id"].default is inspect.Parameter.empty
    assert parameters["independent_trial_count"].default is inspect.Parameter.empty
    assert parameters["risk_free_per_period"].default == Decimal(0)
    assert isinstance(_dsr(), Decimal)


def test_not_exported_at_package_root():
    assert not hasattr(validation_package, "compute_deflated_sharpe_ratio")


# ================================================================
# validation order and exact messages (criteria 3-11)
# ================================================================


def test_step1_group_type():
    _raises_exactly(
        TypeError,
        "group must be a TrialGroup, got tuple",
        compute_deflated_sharpe_ratio,
        (),
        selected_candidate_id=1,
        independent_trial_count=True,
    )


def test_step2_selected_candidate_id_type():
    _raises_exactly(
        TypeError,
        "selected_candidate_id must be a str, got int",
        _dsr,
        selected_candidate_id=1,
        independent_trial_count=True,
    )


@pytest.mark.parametrize("value", [True, 5.0, Decimal(5), "5", None])
def test_step3_independent_trial_count_type(value):
    _raises_exactly(
        TypeError,
        f"independent_trial_count must be an int, got {type(value).__name__}",
        _dsr,
        independent_trial_count=value,
        risk_free_per_period=0.0,
    )


@pytest.mark.parametrize("value", [1, 0, -5, 10**30 + 1])
def test_step4_independent_trial_count_range(value):
    _raises_exactly(
        ValueError,
        f"independent_trial_count must be between 2 and 10**30 inclusive, got {value}",
        _dsr,
        independent_trial_count=value,
        risk_free_per_period=0.0,
    )


@pytest.mark.parametrize("value", [2, 10**30])
def test_step4_independent_trial_count_bounds_are_accepted(value):
    result = _dsr(independent_trial_count=value)
    assert Decimal(0) <= result <= Decimal(1)


def test_step5_risk_free_type_and_finiteness():
    single = TrialGroup(group_id="g", trials=(_group().trials[0],))
    _raises_exactly(
        TypeError,
        "risk_free_per_period must be a Decimal, got float",
        _dsr,
        single,
        risk_free_per_period=0.0,
    )
    for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        _raises_exactly(
            ValueError,
            f"risk_free_per_period must be finite, got {value}",
            _dsr,
            single,
            risk_free_per_period=value,
        )


def test_step6_single_trial_group_is_rejected_before_selected_lookup():
    single = TrialGroup(group_id="g", trials=(_group().trials[0],))
    _raises_exactly(
        ValueError,
        "at least two trials are required to estimate trial Sharpe variance, got 1",
        _dsr,
        single,
        selected_candidate_id="missing",
    )


def test_step7_selected_not_in_group_is_rejected_before_window_check():
    two_window = _two_window_group()
    _raises_exactly(
        ValueError,
        "selected_candidate_id 'missing' is not in the group",
        _dsr,
        two_window,
        selected_candidate_id="missing",
    )


def _two_window_group():
    second = TemporalWindow(start=START + timedelta(hours=8), end=START + timedelta(hours=16))
    trials = tuple(
        _trial(
            candidate_id,
            (
                WindowResult(window=WINDOW, result=_result(curve)),
                WindowResult(window=second, result=_result(curve)),
            ),
        )
        for candidate_id, curve in SYNTHETIC_CURVES.items()
    )
    return TrialGroup(group_id="g", trials=trials)


def test_step8_multi_window_trial_is_rejected():
    _raises_exactly(
        ValueError,
        "trials[0] must have exactly one window result for deflated Sharpe, got 2",
        _dsr,
        _two_window_group(),
    )


def test_step8_multi_window_wins_over_step9_unequal_lengths():
    second = TemporalWindow(start=START + timedelta(hours=8), end=START + timedelta(hours=16))
    lengths = {"alpha": SYNTHETIC_CURVES["alpha"], "short": ("1001", "1003")}
    trials = tuple(
        _trial(
            candidate_id,
            (
                WindowResult(window=WINDOW, result=_result(curve)),
                WindowResult(window=second, result=_result(curve)),
            ),
        )
        for candidate_id, curve in lengths.items()
    )
    _raises_exactly(
        ValueError,
        "trials[0] must have exactly one window result for deflated Sharpe, got 2",
        _dsr,
        TrialGroup(group_id="g", trials=trials),
    )


def test_step9_unequal_equity_observation_counts_are_rejected_before_stage2():
    curves = {
        "alpha": SYNTHETIC_CURVES["alpha"],
        "flat": ("1000", "1000", "1000", "1000", "1000", "1000", "1000", "1000"),
        "short": ("1001", "1003"),
    }
    _raises_exactly(
        ValueError,
        "trials[2] has 2 equity observations, trials[0] has 8",
        _dsr,
        _group(curves),
    )


def test_step10_stage2_errors_propagate_unchanged():
    flat_curve = ("1000",) * 8
    curves = {"alpha": SYNTHETIC_CURVES["alpha"], "flat": flat_curve}
    with pytest.raises(ValueError) as stage2_error:
        compute_stage2_metrics(_result(flat_curve))
    _raises_exactly(ValueError, str(stage2_error.value), _dsr, _group(curves))


def test_step10_single_observation_stage2_error_propagates_unchanged():
    curves = {"alpha": ("1010",), "beta": ("1020",)}
    with pytest.raises(ValueError) as stage2_error:
        compute_stage2_metrics(_result(("1010",)))
    _raises_exactly(ValueError, str(stage2_error.value), _dsr, _group(curves))


def test_step10_wins_over_step11_zero_variance():
    curves = {
        "alpha": SYNTHETIC_CURVES["alpha"],
        "alpha_copy": SYNTHETIC_CURVES["alpha"],
        "flat": ("1000",) * 8,
    }
    with pytest.raises(ValueError) as excinfo:
        _dsr(_group(curves))
    assert str(excinfo.value).startswith("return_stdev must be greater than zero")


def test_step11_zero_trial_sharpe_variance_is_rejected():
    curves = {"alpha": SYNTHETIC_CURVES["alpha"], "alpha_copy": SYNTHETIC_CURVES["alpha"]}
    _raises_exactly(
        ValueError,
        "trial Sharpe ratio variance must be greater than zero, got 0E-56",
        _dsr,
        _group(curves),
    )


def test_step13_nonpositive_variance_term_is_rejected_not_clipped():
    # Unreachable with moments of a real sample (Pearson: kurtosis >= skewness^2 + 1
    # makes the term a non-negative quadratic in SR, Bölüm 17.4.9), so it is
    # exercised through the locked private helper with inconsistent statistics.
    _raises_exactly(
        ValueError,
        "deflated Sharpe variance term must be greater than zero, got -3.5",
        dsr_module._deflated_sharpe_from_statistics,
        sharpe_ratio=Decimal(1),
        trial_sharpe_variance=Decimal("0.01"),
        independent_trial_count=10,
        sample_length=100,
        skewness=Decimal(5),
        kurtosis=Decimal(3),
    )


def test_real_sample_variance_term_stays_positive_near_two_point_extreme():
    # Two-point-like returns push skewness/kurtosis toward the Pearson boundary;
    # the result is still a valid probability (no clip, no error).
    curves = {
        "alpha": ("1045", "1092.025", "1141.16612", "1192.518595", "1789", "1869.505", "1953.63", "2041.54"),
        "beta": SYNTHETIC_CURVES["beta"],
    }  # fmt: skip
    result = _dsr(_group(curves), independent_trial_count=3)
    assert Decimal(0) <= result <= Decimal(1)


# ================================================================
# semantics: N, V, moments, scale, rf, selection (criteria 5, 12-16, 23)
# ================================================================


def test_selected_trial_need_not_have_the_highest_sharpe():
    group = _group()
    sharpes = {
        trial.candidate.candidate_id: compute_stage2_metrics(trial.results[0].result).sharpe_ratio
        for trial in group.trials
    }
    lowest = min(sharpes, key=sharpes.__getitem__)
    assert lowest != max(sharpes, key=sharpes.__getitem__)
    result = _dsr(group, selected_candidate_id=lowest)
    assert Decimal(0) <= result <= Decimal(1)


def test_recorded_trial_count_is_never_used_as_n():
    group = _group()  # three recorded trials
    below = _dsr(group, independent_trial_count=2)
    equal = _dsr(group, independent_trial_count=3)
    above = _dsr(group, independent_trial_count=100)
    assert below > equal > above


@pytest.mark.parametrize(("rf", "selected", "n", "reference"), GROUP_REFERENCES)
def test_group_result_matches_independent_high_precision_reference(rf, selected, n, reference):
    result = _dsr(
        selected_candidate_id=selected,
        independent_trial_count=n,
        risk_free_per_period=Decimal(rf),
    )
    assert _abs_diff(result, reference) <= DSR_ABS_TOLERANCE


@pytest.mark.parametrize("n", [2, 5, 50])
@pytest.mark.parametrize("selected", ["alpha", "beta", "gamma"])
def test_group_result_matches_independent_float_reference(selected, n):
    result = _dsr(selected_candidate_id=selected, independent_trial_count=n)
    reference = _float_reference_dsr(SYNTHETIC_CURVES, selected, n)
    assert abs(float(result) - reference) <= 1e-9


def test_moment_convention_is_population_moments_with_raw_kurtosis():
    result = float(_dsr(selected_candidate_id="alpha", independent_trial_count=5))
    population_raw = _float_reference_dsr(SYNTHETIC_CURVES, "alpha", 5)
    sample_corrected = _float_reference_dsr(SYNTHETIC_CURVES, "alpha", 5, sample_moments=True)
    excess = _float_reference_dsr(SYNTHETIC_CURVES, "alpha", 5, excess=True)
    assert abs(result - population_raw) <= 1e-9
    assert abs(result - sample_corrected) > 1e-6
    assert abs(result - excess) > 1e-6


def test_risk_free_rate_is_applied_to_every_trial_sharpe():
    rf = Decimal("0.0005")
    result = _dsr(risk_free_per_period=rf)
    reference_all = _float_reference_dsr(SYNTHETIC_CURVES, "alpha", 5, rf=0.0005)
    assert abs(float(result) - reference_all) <= 1e-9
    assert result != _dsr()


def test_scale_is_per_observation_and_independent_of_timestamp_spacing():
    hourly = _dsr(_group(spacing=timedelta(hours=1)))
    daily_window = TemporalWindow(start=START, end=START + timedelta(days=8))
    daily = _dsr(_group(spacing=timedelta(days=1), window=daily_window))
    assert hourly == daily


def test_dsr_does_not_increase_as_n_grows():
    values = [_dsr(independent_trial_count=n) for n in (2, 3, 5, 10, 46, 100, 10**6, 10**30)]
    assert all(later <= earlier for earlier, later in itertools.pairwise(values))
    assert values[0] > values[-1]


# ================================================================
# paper example and numerics (criteria 17-21)
# ================================================================


@pytest.mark.parametrize(("n", "skewness", "kurtosis", "reference"), PAPER_REFERENCES)
def test_paper_example_matches_high_precision_reference(n, skewness, kurtosis, reference):
    result = dsr_module._deflated_sharpe_from_statistics(
        sharpe_ratio=PAPER_SR,
        trial_sharpe_variance=PAPER_V,
        independent_trial_count=n,
        sample_length=PAPER_T,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    assert _abs_diff(result, reference) <= DSR_ABS_TOLERANCE


def test_paper_example_published_four_decimal_values():
    def dsr(n, skewness, kurtosis):
        value = dsr_module._deflated_sharpe_from_statistics(
            sharpe_ratio=PAPER_SR,
            trial_sharpe_variance=PAPER_V,
            independent_trial_count=n,
            sample_length=PAPER_T,
            skewness=skewness,
            kurtosis=kurtosis,
        )
        return value.quantize(Decimal("0.0001"))

    assert dsr(100, Decimal(-3), Decimal(10)) == Decimal("0.9004")
    assert dsr(46, Decimal(-3), Decimal(10)) == Decimal("0.9505")
    assert dsr(88, Decimal(0), Decimal(3)) == Decimal("0.9505")


def test_paper_example_normal_returns_threshold_is_n_88():
    def dsr(n):
        return dsr_module._deflated_sharpe_from_statistics(
            sharpe_ratio=PAPER_SR,
            trial_sharpe_variance=PAPER_V,
            independent_trial_count=n,
            sample_length=PAPER_T,
            skewness=Decimal(0),
            kurtosis=Decimal(3),
        )

    assert dsr(88) >= Decimal("0.95")
    assert dsr(89) < Decimal("0.95")


@pytest.mark.parametrize(("x", "reference"), CDF_REFERENCES)
def test_normal_cdf_matches_independent_reference(x, reference):
    assert _abs_diff(dsr_module._normal_cdf(Decimal(x)), reference) <= CDF_ABS_TOLERANCE


@pytest.mark.parametrize(("x", "max_relative_error"), [("-10", "1e-50"), ("-14.5", "1e-28")])
def test_normal_cdf_lower_tail_relative_error(x, max_relative_error):
    reference = dict(CDF_REFERENCES)[x]
    with localcontext(Context(prec=150)):
        relative = _abs_diff(dsr_module._normal_cdf(Decimal(x)), reference) / Decimal(reference)
    assert relative <= Decimal(max_relative_error)


def test_normal_cdf_clamp_boundaries():
    assert dsr_module._normal_cdf(Decimal(15)) == Decimal(1)
    assert dsr_module._normal_cdf(Decimal("15.000001")) == Decimal(1)
    assert dsr_module._normal_cdf(Decimal(-15)) == Decimal(0)
    assert dsr_module._normal_cdf(Decimal(-40)) == Decimal(0)
    assert Decimal(0) < dsr_module._normal_cdf(Decimal("-14.999999")) < Decimal("1e-50")
    assert dsr_module._normal_cdf(Decimal("14.999999")) < Decimal(1)


def test_normal_cdf_agrees_with_float_reference_on_grid():
    nd = statistics.NormalDist()
    for index in range(-1500, 1501, 25):
        x = Decimal(index) / 100
        assert abs(float(dsr_module._normal_cdf(x)) - nd.cdf(float(x))) <= 1e-15


def test_normal_cdf_symmetry_identity():
    for x in ("0.3", "1.7", "4.2", "9.9", "13.1"):
        with localcontext(Context(prec=150)):
            total = dsr_module._normal_cdf(Decimal(x)) + dsr_module._normal_cdf(-Decimal(x))
            assert abs(total - 1) <= Decimal("1e-75")


@pytest.mark.parametrize(("p", "reference"), QUANTILE_REFERENCES)
def test_normal_quantile_matches_independent_reference(p, reference):
    assert _abs_diff(dsr_module._normal_quantile(Decimal(p)), reference) <= QUANTILE_X_TOLERANCE


def test_normal_quantile_half_is_exact_zero_and_domain_is_enforced():
    assert dsr_module._normal_quantile(Decimal("0.5")) == Decimal(0)
    for p in ("0.4999", "1", "1.5", "-1"):
        _raises_exactly(
            ValueError,
            f"p must satisfy 1/2 <= p < 1, got {p}",
            dsr_module._normal_quantile,
            Decimal(p),
        )


def test_normal_quantile_roundtrip_consistency_is_supplementary_only():
    for p in ("0.6", "0.9", "0.999999"):
        x = dsr_module._normal_quantile(Decimal(p))
        assert _abs_diff(dsr_module._normal_cdf(x), p) <= Decimal("1e-60")


@pytest.mark.parametrize("n", [2, 10, 100, 10**6, 10**12, 10**30])
def test_newton_converges_within_limit_for_sampled_n(n, monkeypatch):
    steps = {"count": 0}
    original_pdf = dsr_module._normal_pdf

    def counting_pdf(x):
        steps["count"] += 1
        return original_pdf(x)

    monkeypatch.setattr(dsr_module, "_normal_pdf", counting_pdf)
    with localcontext(dsr_module._dsr_context()):
        n_decimal = Decimal(n)
        probabilities = (
            Decimal(1) - Decimal(1) / n_decimal,
            Decimal(1) - Decimal(1) / (n_decimal * Decimal(1).exp()),
        )
    for p in probabilities:
        steps["count"] = 0
        dsr_module._normal_quantile(p)
        # each Newton step evaluates the PDF twice (directly and inside the CDF)
        assert steps["count"] // 2 < dsr_module._QUANTILE_MAX_ITERATIONS


def test_newton_non_convergence_fails_closed(monkeypatch):
    monkeypatch.setattr(dsr_module, "_QUANTILE_MAX_ITERATIONS", 1)
    _raises_exactly(
        ArithmeticError,
        "normal quantile Newton iteration did not converge within 1 iterations",
        dsr_module._normal_quantile,
        Decimal("0.99"),
    )


def test_cdf_series_non_convergence_fails_closed(monkeypatch):
    monkeypatch.setattr(dsr_module, "_CDF_MAX_TERMS", 1)
    _raises_exactly(
        ArithmeticError,
        "normal CDF series did not converge within 1 terms",
        dsr_module._normal_cdf,
        Decimal(3),
    )


def test_constants_match_published_leading_digits():
    assert str(dsr_module._EULER_MASCHERONI).startswith(
        "0.57721566490153286060651209008240243104215933593992"
    )
    assert str(dsr_module._PI).startswith("3.14159265358979323846264338327950288419716939937510")
    assert len(dsr_module._EULER_MASCHERONI.as_tuple().digits) >= 50
    assert len(dsr_module._PI.as_tuple().digits) >= 50


# ================================================================
# rounding, ambient context, purity, imports (criteria 22, 24, 25)
# ================================================================


def test_result_is_probability_rounded_to_28_significant_digits():
    for n in (2, 5, 50, 10**30):
        result = _dsr(independent_trial_count=n)
        assert Decimal(0) <= result <= Decimal(1)
        assert len(result.as_tuple().digits) <= 28


def test_ambient_decimal_context_does_not_change_result():
    baseline = _dsr(selected_candidate_id="gamma", independent_trial_count=50)
    hostile = Context(
        prec=3,
        rounding=decimal.ROUND_DOWN,
        traps=[decimal.Inexact, decimal.Rounded, decimal.DivisionByZero],
    )
    with localcontext(hostile):
        assert _dsr(selected_candidate_id="gamma", independent_trial_count=50) == baseline


def test_no_mutation_and_deterministic_repeated_calls():
    group = _group()
    trials_before = group.trials
    snapshot = hash(group)
    first = _dsr(group)
    second = _dsr(group)
    assert first == second
    assert group.trials is trials_before
    assert hash(group) == snapshot


def _import_lines(module):
    return [
        line.strip()
        for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]


def test_import_direction_is_locked():
    lines = _import_lines(dsr_module)
    modules = {line.split()[1] for line in lines}
    assert modules == {
        "decimal",
        "crypto_quant_lab.validation.metrics",
        "crypto_quant_lab.validation.trial_group",
    }
    source = inspect.getsource(dsr_module)
    for forbidden in ("float(", "import math", "import statistics", "import random", "import time"):
        assert forbidden not in source
    for private in ("_metrics_decimal_context", "_compute_periodic_returns_unchecked"):
        assert private not in source


@pytest.mark.parametrize(
    "module",
    [
        candidate_module,
        trial_group_module,
        metrics_module,
        annualized_metrics_module,
        rolling_module,
        windows_module,
        purging_module,
        validation_package,
    ],
    ids=lambda module: module.__name__,
)
def test_no_existing_module_imports_deflated_sharpe(module):
    for line in _import_lines(module):
        assert "deflated_sharpe" not in line


# ================================================================
# real SQLite / rolling integration (criterion 27)
# ================================================================

PRICES = (
    "100", "102", "99", "103", "101", "106", "104", "108", "103", "107",
    "110", "105", "109", "112", "108", "113", "111", "116", "112", "118",
    "115", "119", "117", "121", "118",
)  # fmt: skip


class _LongPolicy:
    def target_position(self, context):
        return PositionTarget.LONG


class _ShortPolicy:
    def target_position(self, context):
        return PositionTarget.SHORT


class _AlternatingPolicy:
    def target_position(self, context):
        return PositionTarget.LONG if len(context.candles) % 2 else PositionTarget.SHORT


def _candle(hour, price):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=START + timedelta(hours=hour),
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _rolling_group(store, windows):
    trials = []
    for candidate_id, policy in (
        ("long", _LongPolicy),
        ("short", _ShortPolicy),
        ("alternating", _AlternatingPolicy),
    ):
        results = run_rolling_backtest_from_store(
            store,
            windows,
            policy_factory=policy,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
            cost_model=ZeroCostModel(),
        )
        trials.append(_trial(candidate_id, results))
    return TrialGroup(group_id="rolling", trials=tuple(trials))


def test_real_sqlite_rolling_integration(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([_candle(hour, price) for hour, price in enumerate(PRICES)])
        window = TemporalWindow(start=START, end=START + timedelta(hours=24))
        group = _rolling_group(store, (window,))

        curves = {
            trial.candidate.candidate_id: tuple(
                str(point.equity) for point in trial.results[0].result.equity_curve
            )
            for trial in group.trials
        }
        values = []
        for n in (3, 10, 100):
            result = compute_deflated_sharpe_ratio(
                group, selected_candidate_id="long", independent_trial_count=n
            )
            assert Decimal(0) <= result <= Decimal(1)
            assert abs(float(result) - _float_reference_dsr(curves, "long", n)) <= 1e-9
            values.append(result)
        assert values[0] >= values[1] >= values[2]

        halves = (
            TemporalWindow(start=START, end=START + timedelta(hours=12)),
            TemporalWindow(start=START + timedelta(hours=12), end=START + timedelta(hours=24)),
        )
        _raises_exactly(
            ValueError,
            "trials[0] must have exactly one window result for deflated Sharpe, got 2",
            compute_deflated_sharpe_ratio,
            _rolling_group(store, halves),
            selected_candidate_id="long",
            independent_trial_count=3,
        )
    finally:
        store.close()
