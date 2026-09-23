"""Probability of Backtest Overfitting via CSCV (VALIDATION_SPEC.md Bölüm 17.5.13-17.5.24, 28.M).

Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest
Overfitting", Algorithm 2.3 (CSCV) and Section 3.1: the rows of a T x N
`TrialReturnMatrix` are split into S contiguous equal blocks; for every one of
the C(S, S/2) block combinations the in-sample (IS) half selects the best
Sharpe ratio and the out-of-sample (OOS) half ranks it; PBO is the rate at
which the IS-optimal candidate lands at or below the OOS median (logit <= 0).

This is a research diagnostic. It never selects a candidate for trading,
never emits orders or risk decisions, and proves nothing about independence,
complete research history or holdout protection. CSCV is not CPCV.
"""

from dataclasses import dataclass as _dataclass
from decimal import ROUND_HALF_EVEN as _ROUND_HALF_EVEN
from decimal import Context as _Context
from decimal import Decimal as _Decimal
from decimal import localcontext as _localcontext
from fractions import Fraction as _Fraction
from itertools import combinations as _combinations
from math import comb as _comb

from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix as _TrialReturnMatrix

_MAX_CELL_EVALUATIONS = 20_000_000


@_dataclass(frozen=True, slots=True)
class CscvCombination:
    """One CSCV split: IS blocks, the IS-optimal candidate(s) and their OOS rank and logit.

    `selected_candidate_ids` holds every candidate tied for the best IS Sharpe
    ratio (matrix column order); `out_of_sample_ranks[i]` and `logits[i]`
    belong to `selected_candidate_ids[i]`.
    """

    in_sample_blocks: tuple[int, ...]
    selected_candidate_ids: tuple[str, ...]
    out_of_sample_ranks: tuple[_Decimal, ...]
    logits: tuple[_Decimal, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "in_sample_blocks",
            "selected_candidate_ids",
            "out_of_sample_ranks",
            "logits",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
        if len(self.selected_candidate_ids) == 0:
            raise ValueError("selected_candidate_ids must not be empty")
        if not (
            len(self.selected_candidate_ids) == len(self.out_of_sample_ranks) == len(self.logits)
        ):
            raise ValueError(
                "selected_candidate_ids, out_of_sample_ranks and logits must have equal length"
            )


@_dataclass(frozen=True, slots=True)
class PboResult:
    """CSCV output: every combination in lexicographic order and the PBO estimate."""

    block_count: int
    combinations: tuple[CscvCombination, ...]
    probability_of_backtest_overfitting: _Decimal

    def __post_init__(self) -> None:
        if isinstance(self.block_count, bool) or not isinstance(self.block_count, int):
            raise TypeError(f"block_count must be an int, got {type(self.block_count).__name__}")
        if not isinstance(self.combinations, tuple):
            raise TypeError(f"combinations must be a tuple, got {type(self.combinations).__name__}")
        for index, combination in enumerate(self.combinations):
            if not isinstance(combination, CscvCombination):
                raise TypeError(
                    f"combinations[{index}] must be a CscvCombination, "
                    f"got {type(combination).__name__}"
                )
        pbo = self.probability_of_backtest_overfitting
        if not isinstance(pbo, _Decimal):
            raise TypeError(
                f"probability_of_backtest_overfitting must be a Decimal, got {type(pbo).__name__}"
            )
        if not pbo.is_finite() or not (_Decimal(0) <= pbo <= _Decimal(1)):
            raise ValueError(
                f"probability_of_backtest_overfitting must be within [0, 1], got {pbo}"
            )


def _pbo_context() -> _Context:
    """A fresh Bölüm 15.17-shaped 28-digit context (same shape as Stage-2 Sharpe)."""
    return _Context(
        prec=28,
        rounding=_ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        traps=[],
    )


def _subsample_sharpe_ratio(
    values: list[_Decimal], risk_free_per_period: _Decimal
) -> _Decimal | None:
    """Stage-2's exact Sharpe formula and operation order (Bölüm 15.16) on raw returns.

    Returns None when the sample standard deviation is zero (Sharpe undefined);
    the caller turns that into an error — it is never replaced by 0 or infinity.
    """
    with _localcontext(_pbo_context()):
        n = len(values)
        return_sum = sum(values, _Decimal(0))
        mean_return = return_sum / _Decimal(n)
        squared_deviation_sum = _Decimal(0)
        for value in values:
            deviation = value - mean_return
            squared_deviation_sum += deviation * deviation
        sample_variance = squared_deviation_sum / _Decimal(n - 1)
        return_stdev = sample_variance.sqrt()
    if not return_stdev.is_finite() or not mean_return.is_finite():
        raise ValueError(
            f"computed subsample statistics must be finite, got mean {mean_return}, "
            f"stdev {return_stdev}"
        )
    if return_stdev <= _Decimal(0):
        return None
    with _localcontext(_pbo_context()):
        sharpe_ratio = (mean_return - risk_free_per_period) / return_stdev
    if not sharpe_ratio.is_finite():
        raise ValueError(f"computed subsample sharpe_ratio must be finite, got {sharpe_ratio}")
    return sharpe_ratio


def _column_sharpes(
    matrix: _TrialReturnMatrix,
    rows: list[int],
    risk_free_per_period: _Decimal,
    *,
    sample_name: str,
    blocks: tuple[int, ...],
) -> list[_Decimal]:
    sharpes = []
    for column, candidate_id in enumerate(matrix.candidate_ids):
        sharpe = _subsample_sharpe_ratio(
            [matrix.returns[row][column] for row in rows], risk_free_per_period
        )
        if sharpe is None:
            raise ValueError(
                f"{sample_name} Sharpe ratio is undefined for candidate {candidate_id!r} "
                f"in combination with in-sample blocks {blocks}: zero standard deviation"
            )
        sharpes.append(sharpe)
    return sharpes


def _average_rank(values: list[_Decimal], column: int) -> _Decimal:
    """Ascending average (mid) rank: 1 = lowest; ties share the mean of their positions."""
    value = values[column]
    below = sum(1 for other in values if other < value)
    equal = sum(1 for other in values if other == value)
    with _localcontext(_pbo_context()):
        return _Decimal(2 * below + equal + 1) / _Decimal(2)


def compute_probability_of_backtest_overfitting(
    matrix: _TrialReturnMatrix,
    *,
    block_count: int,
    risk_free_per_period: _Decimal = _Decimal(0),
) -> PboResult:
    """CSCV estimate of PBO for `matrix` split into `block_count` blocks (Bölüm 17.5.13-17.5.24)."""
    if not isinstance(matrix, _TrialReturnMatrix):
        raise TypeError(f"matrix must be a TrialReturnMatrix, got {type(matrix).__name__}")  # 1
    if isinstance(block_count, bool) or not isinstance(block_count, int):
        raise TypeError(f"block_count must be an int, got {type(block_count).__name__}")  # 2
    if block_count < 2 or block_count % 2 != 0:
        raise ValueError(f"block_count must be an even integer >= 2, got {block_count}")  # 3
    if not isinstance(risk_free_per_period, _Decimal):
        raise TypeError(
            f"risk_free_per_period must be a Decimal, got {type(risk_free_per_period).__name__}"
        )  # 4
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")

    candidate_count = len(matrix.candidate_ids)
    if candidate_count < 2:
        raise ValueError(
            "at least two candidates are required to rank out-of-sample performance, "
            f"got {candidate_count}"
        )  # 5
    row_count = len(matrix.returns)
    if row_count % block_count != 0:
        raise ValueError(
            f"row count {row_count} is not divisible by block_count {block_count}; "
            "rows are never trimmed or padded"
        )  # 6
    if row_count // 2 < 2:
        raise ValueError(
            "each half-sample must contain at least two rows to compute a sample standard "
            f"deviation, got {row_count // 2}"
        )  # 7
    combination_count = _comb(block_count, block_count // 2)
    cell_evaluations = combination_count * row_count * candidate_count
    if cell_evaluations > _MAX_CELL_EVALUATIONS:
        raise ValueError(
            f"CSCV cost of {cell_evaluations} cell evaluations "
            f"(C({block_count}, {block_count // 2})={combination_count} x T={row_count} "
            f"x N={candidate_count}) exceeds the limit of {_MAX_CELL_EVALUATIONS}; "
            "no sampling is performed"
        )  # 8

    block_size = row_count // block_count
    all_blocks = range(block_count)
    results: list[CscvCombination] = []
    overfit_total = _Fraction(0)
    for in_sample_blocks in _combinations(all_blocks, block_count // 2):  # 9, lazily
        out_of_sample_blocks = tuple(b for b in all_blocks if b not in in_sample_blocks)
        in_rows = [
            row for b in in_sample_blocks for row in range(b * block_size, (b + 1) * block_size)
        ]
        out_rows = [
            row for b in out_of_sample_blocks for row in range(b * block_size, (b + 1) * block_size)
        ]
        in_sharpes = _column_sharpes(
            matrix,
            in_rows,
            risk_free_per_period,
            sample_name="in-sample",
            blocks=in_sample_blocks,
        )
        out_sharpes = _column_sharpes(
            matrix,
            out_rows,
            risk_free_per_period,
            sample_name="out-of-sample",
            blocks=in_sample_blocks,
        )
        best = max(in_sharpes)
        selected = [column for column, value in enumerate(in_sharpes) if value == best]

        ranks = []
        logits = []
        overfit_selected = 0
        for column in selected:
            rank = _average_rank(out_sharpes, column)
            ranks.append(rank)
            with _localcontext(_pbo_context()):
                # λ <= 0  <=>  ω̄ <= 1/2  <=>  2 r̄ <= N + 1, decided exactly (no ln rounding)
                if _Decimal(2) * rank <= _Decimal(candidate_count + 1):
                    overfit_selected += 1
                logits.append((rank / (_Decimal(candidate_count + 1) - rank)).ln())
        overfit_total += _Fraction(overfit_selected, len(selected))
        results.append(
            CscvCombination(
                in_sample_blocks=in_sample_blocks,
                selected_candidate_ids=tuple(matrix.candidate_ids[c] for c in selected),
                out_of_sample_ranks=tuple(ranks),
                logits=tuple(logits),
            )
        )

    probability = overfit_total / combination_count
    with _localcontext(_pbo_context()):
        pbo = _Decimal(probability.numerator) / _Decimal(probability.denominator)
    return PboResult(
        block_count=block_count,
        combinations=tuple(results),
        probability_of_backtest_overfitting=pbo,
    )
