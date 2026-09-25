"""Effective number of trials and multi-window (pooled) Deflated Sharpe (VALIDATION_SPEC.md Bölüm 17.4.18-17.4.24, 28.U).

Effective N — the average-correlation interpolation of Bailey & López de
Prado's Deflated Sharpe paper (Appendix A.3, named in Bölüm 17.4.14):

    N_eff = rho_bar + (1 - rho_bar) * M

with rho_bar the mean pairwise Pearson correlation of the M trial return
columns of an aligned `TrialReturnMatrix`. rho_bar = 1 gives 1, rho_bar = 0
gives M; a negative rho_bar gives N_eff > M, which is reported and flagged,
never clamped. A zero-variance column makes the correlation undefined -> error.
N_eff is a real number and is NOT fed into DSR automatically: DSR's N stays a
caller-declared integer (Bölüm 17.4.2).

Pooled DSR — the locked DSR computation (`_deflated_sharpe_from_statistics`,
Bölüm 17.4) applied to each trial's windows pooled by concatenating their
periodic returns (every window starts from its own initial cash, so the
capital reset is part of the returns). Sharpe ratios are the Stage-2-identical
subsample Sharpe (PBO's helper), moments DSR's population moments of the
selected trial's pooled returns. Same assumptions as DSR (i.i.d. returns),
extended across window boundaries.
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext

from crypto_quant_lab.validation.deflated_sharpe import (
    _deflated_sharpe_from_statistics,
    _dsr_context,
    _output_context,
)
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns as _compute_periodic_returns,
)
from crypto_quant_lab.validation.pbo import _subsample_sharpe_ratio
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix as _TrialReturnMatrix
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup


@_dataclass(frozen=True, slots=True)
class EffectiveTrialCount:
    trial_count: int
    pair_count: int
    mean_correlation: _Decimal
    effective_trial_count: _Decimal
    exceeds_trial_count: bool  # negative mean correlation: N_eff > M, reported, not clamped


def estimate_effective_trial_count(matrix: _TrialReturnMatrix) -> EffectiveTrialCount:
    """Average-correlation effective N of the matrix columns (Bölüm 17.4.19)."""
    if not isinstance(matrix, _TrialReturnMatrix):
        raise TypeError(f"matrix must be a TrialReturnMatrix, got {type(matrix).__name__}")
    count = len(matrix.candidate_ids)
    if count < 2:
        raise ValueError(f"at least two trials are required, got {count}")
    rows = len(matrix.returns)
    if rows < 2:
        raise ValueError(f"at least two observations are required, got {rows}")
    with _localcontext(_dsr_context()):
        columns = [[row[c] for row in matrix.returns] for c in range(count)]
        centered = []
        for index, column in enumerate(columns):
            mean = sum(column, _Decimal(0)) / _Decimal(rows)
            deviations = [value - mean for value in column]
            norm = sum((d * d for d in deviations), _Decimal(0)).sqrt()
            if norm == 0:
                raise ValueError(
                    f"candidate {matrix.candidate_ids[index]!r} has zero return variance: "
                    "correlation is undefined"
                )
            centered.append((deviations, norm))
        total = _Decimal(0)
        pairs = 0
        for i in range(count):
            for j in range(i + 1, count):
                (a, na), (b, nb) = centered[i], centered[j]
                total += sum((x * y for x, y in zip(a, b, strict=True)), _Decimal(0)) / (na * nb)
                pairs += 1
        rho = total / _Decimal(pairs)
        effective = rho + (_Decimal(1) - rho) * _Decimal(count)
    output = _output_context()
    return EffectiveTrialCount(
        trial_count=count,
        pair_count=pairs,
        mean_correlation=output.plus(rho),
        effective_trial_count=output.plus(effective),
        exceeds_trial_count=effective > count,
    )


def compute_pooled_deflated_sharpe_ratio(
    group: _TrialGroup,
    *,
    selected_candidate_id: str,
    independent_trial_count: int,
    risk_free_per_period: _Decimal = _Decimal(0),
) -> _Decimal:
    """DSR of the selected trial with every trial's windows pooled (Bölüm 17.4.20-17.4.22)."""
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
    if not isinstance(selected_candidate_id, str):
        raise TypeError("selected_candidate_id must be a str")
    if isinstance(independent_trial_count, bool) or not isinstance(independent_trial_count, int):
        raise TypeError("independent_trial_count must be an int")
    if not 2 <= independent_trial_count <= 10**30:
        raise ValueError(
            "independent_trial_count must be between 2 and 10**30 inclusive, "
            f"got {independent_trial_count}"
        )
    if not isinstance(risk_free_per_period, _Decimal) or not risk_free_per_period.is_finite():
        raise ValueError("risk_free_per_period must be a finite Decimal")
    trials = group.trials
    if len(trials) < 2:
        raise ValueError(
            f"at least two trials are required to estimate trial Sharpe variance, got {len(trials)}"
        )
    ids = [trial.candidate.candidate_id for trial in trials]
    if selected_candidate_id not in ids:
        raise ValueError(f"selected_candidate_id {selected_candidate_id!r} is not in the group")
    pooled = [
        [value for r in trial.results for value in _compute_periodic_returns(r.result)]
        for trial in trials
    ]
    for index, returns in enumerate(pooled):
        if len(returns) != len(pooled[0]):
            raise ValueError(
                f"trials[{index}] has {len(returns)} pooled returns, trials[0] has {len(pooled[0])}"
            )
    sharpes = []
    for index, returns in enumerate(pooled):
        sharpe = _subsample_sharpe_ratio(returns, risk_free_per_period)
        if sharpe is None:
            raise ValueError(f"trials[{index}] pooled Sharpe ratio is undefined: zero deviation")
        sharpes.append(sharpe)
    with _localcontext(_dsr_context()):
        m = _Decimal(len(sharpes))
        mean_sharpe = sum(sharpes, _Decimal(0)) / m
        trial_variance = sum(((s - mean_sharpe) ** 2 for s in sharpes), _Decimal(0)) / (m - 1)
    if trial_variance <= 0:
        raise ValueError(
            f"trial Sharpe ratio variance must be greater than zero, got {trial_variance}"
        )
    selected = pooled[ids.index(selected_candidate_id)]
    with _localcontext(_dsr_context()):
        n = _Decimal(len(selected))
        mean = sum(selected, _Decimal(0)) / n
        m2 = sum(((v - mean) ** 2 for v in selected), _Decimal(0)) / n
        m3 = sum(((v - mean) ** 3 for v in selected), _Decimal(0)) / n
        m4 = sum(((v - mean) ** 4 for v in selected), _Decimal(0)) / n
        skewness = m3 / (m2 * m2.sqrt())
        kurtosis = m4 / (m2 * m2)
    return _deflated_sharpe_from_statistics(
        sharpe_ratio=sharpes[ids.index(selected_candidate_id)],
        trial_sharpe_variance=trial_variance,
        independent_trial_count=independent_trial_count,
        sample_length=len(selected),
        skewness=skewness,
        kurtosis=kurtosis,
    )
