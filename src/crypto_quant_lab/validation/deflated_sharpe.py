"""Deflated Sharpe ratio (VALIDATION_SPEC.md Bölüm 17.4.1-17.4.15, 28.K).

Bailey & López de Prado (2014), Journal of Portfolio Management 40(5), Eq. (2):
a Probabilistic Sharpe Ratio whose rejection threshold is the expected maximum
Sharpe ratio of `N` independent trials (Eq. (1)). Computed for ONE explicitly
selected trial of ONE `TrialGroup` of single-window trials, at per-observation
scale, with the trial Sharpe variance taken from that group.

This module never selects, ranks or scores a trial, never derives `N` from
`recorded_trial_count`, and provides no final-holdout protection. `N` is a
caller-declared assumption about independent trials (Bölüm 17.4.2). Arithmetic
is Decimal-only (no float, `math` or `statistics`) in a private 80-digit
context; the result is rounded once to 28 significant digits.
"""

from decimal import ROUND_HALF_EVEN as _ROUND_HALF_EVEN
from decimal import Context as _Context
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext

from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns as _compute_periodic_returns,
)
from crypto_quant_lab.validation.metrics import compute_stage2_metrics as _compute_stage2_metrics
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup
from crypto_quant_lab.validation.trial_group import recorded_trial_count as _recorded_trial_count

# 85 significant digits each (Bölüm 17.4.7); cross-checked against mpmath and
# the published leading digits of both constants.
_EULER_MASCHERONI = _Decimal(
    "0.5772156649015328606065120900824024310421593359399235988057672348848677267776646709369"
)
_PI = _Decimal(
    "3.141592653589793238462643383279502884197169399375105820974944592307816406286208998628"
)

_MAX_INDEPENDENT_TRIAL_COUNT = 10**30
_CDF_CLAMP = _Decimal(15)
_CDF_MAX_TERMS = 5000
_QUANTILE_MAX_ITERATIONS = 200
_QUANTILE_STEP_TOLERANCE = _Decimal("1e-45")


def _dsr_context() -> _Context:
    """A fresh private 80-digit context — same shape as Bölüm 15.17, prec=80."""
    return _Context(
        prec=80,
        rounding=_ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        traps=[],
    )


def _output_context() -> _Context:
    """The Bölüm 15.17 28-digit context, used once to round the final result."""
    return _Context(
        prec=28,
        rounding=_ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        traps=[],
    )


def _normal_pdf(x: _Decimal) -> _Decimal:
    with _localcontext(_dsr_context()):
        return (-(x * x) / _Decimal(2)).exp() / (_Decimal(2) * _PI).sqrt()


def _normal_cdf(x: _Decimal) -> _Decimal:
    """Standard normal CDF: clamp at |x| >= 15, else the Taylor series (Bölüm 17.4.8).

    Φ(x) = 1/2 + φ(x) * (x + x^3/3 + x^5/(3*5) + ...). Every term has the sign
    of `x`, so the series itself has no cancellation; it stops once adding a
    term no longer changes the running sum at working precision.
    """
    if x >= _CDF_CLAMP:
        return _Decimal(1)
    if x <= -_CDF_CLAMP:
        return _Decimal(0)
    with _localcontext(_dsr_context()):
        x_squared = x * x
        term = x
        total = x
        k = 0
        while True:
            k += 1
            if k > _CDF_MAX_TERMS:
                raise ArithmeticError(
                    f"normal CDF series did not converge within {_CDF_MAX_TERMS} terms"
                )
            term = term * x_squared / _Decimal(2 * k + 1)
            if total + term == total:
                break
            total += term
        return _Decimal("0.5") + _normal_pdf(x) * total


def _normal_quantile(p: _Decimal) -> _Decimal:
    """Standard normal quantile for 1/2 <= p < 1 by Newton from x0 = 0 (Bölüm 17.4.8).

    Φ is concave on [0, inf), so iterates increase monotonically toward the
    root without overshoot. Non-convergence within the iteration limit fails
    closed with `ArithmeticError`; it never returns an unconverged value.
    """
    if not (_Decimal("0.5") <= p < _Decimal(1)):
        raise ValueError(f"p must satisfy 1/2 <= p < 1, got {p}")
    with _localcontext(_dsr_context()):
        x = _Decimal(0)
        for _ in range(_QUANTILE_MAX_ITERATIONS):
            step = (p - _normal_cdf(x)) / _normal_pdf(x)
            x = x + step
            if abs(step) <= _QUANTILE_STEP_TOLERANCE:
                return x
    raise ArithmeticError(
        f"normal quantile Newton iteration did not converge within "
        f"{_QUANTILE_MAX_ITERATIONS} iterations"
    )


def _deflated_sharpe_from_statistics(
    *,
    sharpe_ratio: _Decimal,
    trial_sharpe_variance: _Decimal,
    independent_trial_count: int,
    sample_length: int,
    skewness: _Decimal,
    kurtosis: _Decimal,
) -> _Decimal:
    """Eq. (1)-(2) on already-validated statistics (validation steps 13-15)."""
    sr = sharpe_ratio
    with _localcontext(_dsr_context()):
        variance_term = (
            _Decimal(1) - skewness * sr + (kurtosis - _Decimal(1)) / _Decimal(4) * sr * sr
        )
    if not variance_term.is_finite() or variance_term <= _Decimal(0):
        raise ValueError(
            f"deflated Sharpe variance term must be greater than zero, got {variance_term}"
        )

    with _localcontext(_dsr_context()):
        n = _Decimal(independent_trial_count)
        e = _Decimal(1).exp()
        q1 = _normal_quantile(_Decimal(1) - _Decimal(1) / n)
        q2 = _normal_quantile(_Decimal(1) - _Decimal(1) / (n * e))
        gamma = _EULER_MASCHERONI
        sr0 = trial_sharpe_variance.sqrt() * ((_Decimal(1) - gamma) * q1 + gamma * q2)
    if not sr0.is_finite():
        raise ValueError(f"computed sr0 must be finite, got {sr0}")

    with _localcontext(_dsr_context()):
        z = (sr - sr0) * _Decimal(sample_length - 1).sqrt() / variance_term.sqrt()
    if not z.is_finite():
        raise ValueError(f"computed z must be finite, got {z}")

    return _output_context().plus(_normal_cdf(z))


def compute_deflated_sharpe_ratio(
    group: _TrialGroup,
    *,
    selected_candidate_id: str,
    independent_trial_count: int,
    risk_free_per_period: _Decimal = _Decimal(0),
) -> _Decimal:
    """Deflated Sharpe ratio of the explicitly selected trial (Bölüm 17.4.3-17.4.6).

    Validation follows Bölüm 17.4.6's exact 15-step order. Stage-2 Sharpe and
    periodic returns are reused unchanged; their errors propagate unchanged.
    """
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")  # 1
    if not isinstance(selected_candidate_id, str):
        raise TypeError(
            f"selected_candidate_id must be a str, got {type(selected_candidate_id).__name__}"
        )  # 2
    if isinstance(independent_trial_count, bool) or not isinstance(independent_trial_count, int):
        raise TypeError(
            f"independent_trial_count must be an int, got {type(independent_trial_count).__name__}"
        )  # 3
    if not 2 <= independent_trial_count <= _MAX_INDEPENDENT_TRIAL_COUNT:
        raise ValueError(
            "independent_trial_count must be between 2 and 10**30 inclusive, "
            f"got {independent_trial_count}"
        )  # 4
    if not isinstance(risk_free_per_period, _Decimal):
        raise TypeError(
            f"risk_free_per_period must be a Decimal, got {type(risk_free_per_period).__name__}"
        )  # 5
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")

    trial_count = _recorded_trial_count(group)
    if trial_count < 2:
        raise ValueError(
            f"at least two trials are required to estimate trial Sharpe variance, got {trial_count}"
        )  # 6

    trials = group.trials
    selected_index = next(
        (
            index
            for index, trial in enumerate(trials)
            if trial.candidate.candidate_id == selected_candidate_id
        ),
        None,
    )
    if selected_index is None:
        raise ValueError(
            f"selected_candidate_id {selected_candidate_id!r} is not in the group"
        )  # 7

    # 8: exactly one window result per trial -- global pass.
    for index, trial in enumerate(trials):
        if len(trial.results) != 1:
            raise ValueError(
                f"trials[{index}] must have exactly one window result for deflated Sharpe, "
                f"got {len(trial.results)}"
            )

    # 9: equal equity-observation count -- global pass against trials[0].
    reference_length = len(trials[0].results[0].result.equity_curve)
    for index in range(1, len(trials)):
        length = len(trials[index].results[0].result.equity_curve)
        if length != reference_length:
            raise ValueError(
                f"trials[{index}] has {length} equity observations, "
                f"trials[0] has {reference_length}"
            )

    # 10: Stage-2 per-observation Sharpe of every trial; errors propagate unchanged.
    sharpe_ratios = [
        _compute_stage2_metrics(
            trial.results[0].result, risk_free_per_period=risk_free_per_period
        ).sharpe_ratio
        for trial in trials
    ]

    # 11: sample variance (M - 1) of the trial Sharpe ratios.
    with _localcontext(_dsr_context()):
        count = _Decimal(len(sharpe_ratios))
        mean_sharpe = sum(sharpe_ratios, _Decimal(0)) / count
        squared_deviation_sum = _Decimal(0)
        for sharpe in sharpe_ratios:
            deviation = sharpe - mean_sharpe
            squared_deviation_sum += deviation * deviation
        trial_sharpe_variance = squared_deviation_sum / (count - _Decimal(1))
    if not trial_sharpe_variance.is_finite() or trial_sharpe_variance <= _Decimal(0):
        raise ValueError(
            f"trial Sharpe ratio variance must be greater than zero, got {trial_sharpe_variance}"
        )

    # 12: population moments of the selected trial's periodic returns.
    returns = _compute_periodic_returns(trials[selected_index].results[0].result)
    sample_length = len(returns)
    with _localcontext(_dsr_context()):
        observations = _Decimal(sample_length)
        mean_return = sum(returns, _Decimal(0)) / observations
        m2 = _Decimal(0)
        m3 = _Decimal(0)
        m4 = _Decimal(0)
        for periodic_return in returns:
            deviation = periodic_return - mean_return
            m2 += deviation**2
            m3 += deviation**3
            m4 += deviation**4
        m2 = m2 / observations
        m3 = m3 / observations
        m4 = m4 / observations
    if not m2.is_finite() or m2 <= _Decimal(0):
        raise ValueError(f"computed m2 must be finite and valid, got {m2}")
    with _localcontext(_dsr_context()):
        skewness = m3 / (m2 * m2.sqrt())
        kurtosis = m4 / (m2 * m2)
    if not skewness.is_finite():
        raise ValueError(f"computed skewness must be finite and valid, got {skewness}")
    if not kurtosis.is_finite():
        raise ValueError(f"computed kurtosis must be finite and valid, got {kurtosis}")

    return _deflated_sharpe_from_statistics(  # 13-15
        sharpe_ratio=sharpe_ratios[selected_index],
        trial_sharpe_variance=trial_sharpe_variance,
        independent_trial_count=independent_trial_count,
        sample_length=sample_length,
        skewness=skewness,
        kurtosis=kurtosis,
    )
