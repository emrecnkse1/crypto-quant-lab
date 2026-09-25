"""PBO side statistics: performance degradation and probability of loss (VALIDATION_SPEC.md Bölüm 17.5.25-17.5.30, 28.V).

Bailey, Borwein, López de Prado & Zhu, §3.2-3.3, on the SAME CSCV
combinations and IS selections as `compute_probability_of_backtest_overfitting`
(which is called first, so validation and selection are identical):

- performance degradation (§3.2): weighted least-squares line of the OOS
  Sharpe ratio of the IS-selected candidate on its IS Sharpe ratio across all
  combinations, OOS = intercept + slope * IS. Tied IS winners share their
  combination's weight (PBO's rule, Bölüm 17.5.15.5). A constant IS Sharpe
  makes the slope undefined -> None, never 0.
- probability of loss (§3.3): the weight-share of combinations whose selected
  candidate has an OOS Sharpe ratio < 0, as an exact fraction.

Stochastic dominance (§3.4) is NOT evaluated: its exact definition was not
verified against the primary source in this delivery. Descriptive research
diagnostics only — no threshold, pass/fail or selection.
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext
from fractions import Fraction as _Fraction
from itertools import combinations as _combinations

from crypto_quant_lab.validation.deflated_sharpe import _dsr_context, _output_context
from crypto_quant_lab.validation.pbo import _column_sharpes
from crypto_quant_lab.validation.pbo import (
    compute_probability_of_backtest_overfitting as _compute_pbo,
)
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix as _TrialReturnMatrix

STOCHASTIC_DOMINANCE_STATUS = (
    "not_evaluated: the §3.4 stochastic-dominance definition was not verified against the "
    "primary source in this delivery"
)


@_dataclass(frozen=True, slots=True)
class PboDiagnostics:
    block_count: int
    combination_count: int
    points: tuple[tuple[_Decimal, _Decimal, _Decimal], ...]  # (IS Sharpe, OOS Sharpe, weight)
    probability_of_loss: _Decimal
    degradation_slope: _Decimal | None
    degradation_intercept: _Decimal | None
    stochastic_dominance: str


def compute_pbo_diagnostics(
    matrix: _TrialReturnMatrix,
    *,
    block_count: int,
    risk_free_per_period: _Decimal = _Decimal(0),
) -> PboDiagnostics:
    """Degradation line and probability of loss over the CSCV combinations (Bölüm 17.5.26-17.5.28)."""
    pbo = _compute_pbo(matrix, block_count=block_count, risk_free_per_period=risk_free_per_period)
    block_size = len(matrix.returns) // block_count
    all_blocks = range(block_count)
    ids = list(matrix.candidate_ids)
    points = []
    loss = _Fraction(0)
    for combination, in_blocks in zip(
        pbo.combinations, _combinations(all_blocks, block_count // 2), strict=True
    ):
        if combination.in_sample_blocks != in_blocks:
            raise AssertionError("CSCV combination order drifted from PBO")  # pragma: no cover
        out_blocks = tuple(b for b in all_blocks if b not in in_blocks)
        in_rows = [r for b in in_blocks for r in range(b * block_size, (b + 1) * block_size)]
        out_rows = [r for b in out_blocks for r in range(b * block_size, (b + 1) * block_size)]
        in_sharpes = _column_sharpes(matrix, in_rows, risk_free_per_period,
                                     sample_name="in-sample", blocks=in_blocks)  # fmt: skip
        out_sharpes = _column_sharpes(matrix, out_rows, risk_free_per_period,
                                      sample_name="out-of-sample", blocks=in_blocks)  # fmt: skip
        selected = [ids.index(c) for c in combination.selected_candidate_ids]
        weight = _Fraction(1, len(selected))
        for column in selected:
            points.append((in_sharpes[column], out_sharpes[column], weight))
            if out_sharpes[column] < 0:
                loss += weight
    count = len(pbo.combinations)
    probability = loss / count
    slope = intercept = None
    with _localcontext(_dsr_context()):
        weights = [_Decimal(w.numerator) / _Decimal(w.denominator) for _, _, w in points]
        total = sum(weights, _Decimal(0))
        x_mean = sum((w * x for (x, _, _), w in zip(points, weights, strict=True)), _Decimal(0))
        x_mean /= total
        y_mean = sum((w * y for (_, y, _), w in zip(points, weights, strict=True)), _Decimal(0))
        y_mean /= total
        sxx = sum((w * (x - x_mean) ** 2 for (x, _, _), w in zip(points, weights, strict=True)),
                  _Decimal(0))  # fmt: skip
        sxy = sum((w * (x - x_mean) * (y - y_mean)
                   for (x, y, _), w in zip(points, weights, strict=True)), _Decimal(0))  # fmt: skip
        if sxx != 0:
            slope = sxy / sxx
            intercept = y_mean - slope * x_mean
        probability_decimal = _Decimal(probability.numerator) / _Decimal(probability.denominator)
    output = _output_context()
    return PboDiagnostics(
        block_count=block_count,
        combination_count=count,
        points=tuple(
            (x, y, output.plus(w_decimal))
            for (x, y, _), w_decimal in zip(points, weights, strict=True)
        ),
        probability_of_loss=output.plus(probability_decimal),
        degradation_slope=None if slope is None else output.plus(slope),
        degradation_intercept=None if intercept is None else output.plus(intercept),
        stochastic_dominance=STOCHASTIC_DOMINANCE_STATUS,
    )
