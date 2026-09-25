"""One-sided Sharpe-ratio p-values and their Holm family correction (VALIDATION_SPEC.md Bölüm 17.6.11-17.6.18, 28.S).

Test statistic: the Probabilistic Sharpe Ratio of Bailey & López de Prado
(2012) at the benchmark SR* = 0 — exactly the z of the locked Deflated
Sharpe computation (Bölüm 17.4, `deflated_sharpe`) with SR0 replaced by 0:

    z = SR * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR^2)
    p = 1 - Phi(z)          H0: per-observation Sharpe ratio <= 0 (one-sided)

SR is the unchanged Stage-2 per-observation Sharpe ratio (excess of
`risk_free_per_period`), T the number of periodic returns, skew/kurt the
population moments DSR uses, Phi DSR's own Decimal normal CDF. The standard
error treats returns as i.i.d.: serial correlation is NOT corrected; the
lag-1 autocorrelation is reported so a reader can see when the assumption is
doubtful. Each trial must hold exactly one window (multi-window pooling is
not defined, as for DSR).

Family: every trial recorded in one `TrialGroup` (m = n). Unrecorded or
failed trials are not in the family, so the Holm adjustment is a lower bound
of what a complete research history would require. The adjusted results feed
no decision: no candidate is selected, ranked or approved here.
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext

from crypto_quant_lab.validation.deflated_sharpe import _dsr_context, _normal_cdf, _output_context
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns as _compute_periodic_returns,
)
from crypto_quant_lab.validation.metrics import compute_stage2_metrics as _compute_stage2_metrics
from crypto_quant_lab.validation.multiple_testing import HolmCorrectionResult as _HolmResult
from crypto_quant_lab.validation.multiple_testing import HypothesisPValue as _HypothesisPValue
from crypto_quant_lab.validation.multiple_testing import (
    apply_holm_correction as _apply_holm_correction,
)
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup

NULL_HYPOTHESIS = (
    "H0: the per-observation Sharpe ratio (excess of risk_free_per_period) is <= 0; one-sided"
)
METHOD = (
    "Probabilistic Sharpe Ratio at SR* = 0 (Bailey & Lopez de Prado 2012), the Deflated Sharpe "
    "z with SR0 = 0; single-window trials only"
)
ASSUMPTION = (
    "returns are treated as i.i.d.; serial correlation is NOT corrected (lag-1 autocorrelation "
    "is reported only); no multi-window pooling"
)
FAMILY_SCOPE = (
    "family = every trial recorded in the TrialGroup (m = n); unrecorded or failed trials are "
    "not corrected for, so the Holm adjustment is a lower bound; results feed no selection, "
    "ranking or approval"
)


@_dataclass(frozen=True, slots=True)
class SharpeSignificance:
    candidate_id: str
    sample_length: int
    sharpe_ratio: _Decimal
    skewness: _Decimal
    kurtosis: _Decimal
    z_statistic: _Decimal
    p_value: _Decimal
    lag1_autocorrelation: _Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.p_value, _Decimal) or not (
            _Decimal(0) <= self.p_value <= _Decimal(1)
        ):
            raise ValueError(f"p_value must be a Decimal within [0, 1], got {self.p_value!r}")


@_dataclass(frozen=True, slots=True)
class SharpeFamilyTest:
    family_id: str
    null_hypothesis: str
    method: str
    assumption: str
    family_scope: str
    results: tuple[SharpeSignificance, ...]
    holm: _HolmResult


def _significance(trial, index: int, risk_free_per_period: _Decimal) -> SharpeSignificance:
    if len(trial.results) != 1:
        raise ValueError(
            f"trials[{index}] must have exactly one window result for a Sharpe p-value, got "
            f"{len(trial.results)} (multi-window pooling is not defined)"
        )
    result = trial.results[0].result
    sharpe = _compute_stage2_metrics(result, risk_free_per_period=risk_free_per_period).sharpe_ratio
    returns = _compute_periodic_returns(result)
    count = len(returns)
    with _localcontext(_dsr_context()):
        n = _Decimal(count)
        mean = sum(returns, _Decimal(0)) / n
        deviations = [value - mean for value in returns]
        m2 = sum((d * d for d in deviations), _Decimal(0)) / n
        m3 = sum((d**3 for d in deviations), _Decimal(0)) / n
        m4 = sum((d**4 for d in deviations), _Decimal(0)) / n
        skewness = m3 / (m2 * m2.sqrt())
        kurtosis = m4 / (m2 * m2)
        variance_term = (
            _Decimal(1) - skewness * sharpe + (kurtosis - _Decimal(1)) / _Decimal(4) * sharpe**2
        )
    if not variance_term.is_finite() or variance_term <= _Decimal(0):
        raise ValueError(
            f"trials[{index}]: Sharpe standard-error term must be greater than zero, "
            f"got {variance_term}"
        )
    with _localcontext(_dsr_context()):
        z = sharpe * _Decimal(count - 1).sqrt() / variance_term.sqrt()
        p_value = _Decimal(1) - _normal_cdf(z)
        autocorrelation = None
        if count >= 3:
            lagged = sum((deviations[t] * deviations[t - 1] for t in range(1, count)), _Decimal(0))
            autocorrelation = lagged / (m2 * n)
    output = _output_context()
    return SharpeSignificance(
        candidate_id=trial.candidate.candidate_id,
        sample_length=count,
        sharpe_ratio=sharpe,
        skewness=output.plus(skewness),
        kurtosis=output.plus(kurtosis),
        z_statistic=output.plus(z),
        p_value=min(max(output.plus(p_value), _Decimal(0)), _Decimal(1)),
        lag1_autocorrelation=None if autocorrelation is None else output.plus(autocorrelation),
    )


def compute_sharpe_significance(
    group: _TrialGroup, *, risk_free_per_period: _Decimal = _Decimal(0)
) -> tuple[SharpeSignificance, ...]:
    """One-sided PSR p-value of every trial in `group`, in group order (Bölüm 17.6.12-17.6.14)."""
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
    if not isinstance(risk_free_per_period, _Decimal):
        raise TypeError(
            f"risk_free_per_period must be a Decimal, got {type(risk_free_per_period).__name__}"
        )
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")
    for index, trial in enumerate(group.trials):  # global pass before any computation
        if len(trial.results) != 1:
            raise ValueError(
                f"trials[{index}] must have exactly one window result for a Sharpe p-value, "
                f"got {len(trial.results)} (multi-window pooling is not defined)"
            )
    return tuple(
        _significance(trial, index, risk_free_per_period)
        for index, trial in enumerate(group.trials)
    )


def evaluate_sharpe_family(
    group: _TrialGroup,
    *,
    significance_level: _Decimal,
    risk_free_per_period: _Decimal = _Decimal(0),
) -> SharpeFamilyTest:
    """p-values of the group's trials + the existing Holm correction (Bölüm 17.6.15-17.6.16)."""
    results = compute_sharpe_significance(group, risk_free_per_period=risk_free_per_period)
    holm = _apply_holm_correction(
        group.group_id,
        tuple(_HypothesisPValue(hypothesis_id=r.candidate_id, p_value=r.p_value) for r in results),
        significance_level=significance_level,
    )
    return SharpeFamilyTest(
        family_id=group.group_id,
        null_hypothesis=NULL_HYPOTHESIS,
        method=METHOD,
        assumption=ASSUMPTION,
        family_scope=FAMILY_SCOPE,
        results=results,
        holm=holm,
    )
