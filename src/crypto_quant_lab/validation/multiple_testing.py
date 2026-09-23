"""Holm multiple-testing correction for a declared family of p-values (VALIDATION_SPEC.md Bölüm 17.6.1-17.6.10, 28.N).

Holm (1979), as implemented by R's `p.adjust(method = "holm")`:
adjusted p_(i) = min(1, max_{j <= i} (m + 1 - j) * p_(j)) over the ascending
p-values of the m hypotheses supplied; a hypothesis is rejected when its
adjusted p-value is <= the explicit significance level.

This is a correction layer only. It does not produce p-values from returns,
Sharpe ratios, DSR or PBO, and the family size is exactly the number of
hypotheses given — no unobserved hypotheses are assumed. A family identifier
does not prove that the hypotheses were pre-specified or that the research
history is complete. Rejection is statistical significance under Holm's
family-wise error control, not a probability of profit or a trading decision.

All arithmetic is exact: products (m + 1 - j) * p are built from the integer
coefficient of p, and only comparisons follow — no Decimal context is used,
so the caller's context cannot change any result.
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal


def _require_canonical_identifier(value: object, field_name: str) -> None:
    """Same rule and messages as `candidate_id` (Bölüm 18.6), redefined locally."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    stripped = value.strip()
    if stripped == "":
        raise ValueError(f"{field_name} must not be empty or whitespace-only")
    if value != stripped:
        raise ValueError(f"{field_name} must not have leading/trailing whitespace padding")


def _require_probability(value: object, field_name: str) -> None:
    if not isinstance(value, _Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite() or not (_Decimal(0) <= value <= _Decimal(1)):
        raise ValueError(f"{field_name} must be finite and within [0, 1], got {value}")


@_dataclass(frozen=True, slots=True)
class HypothesisPValue:
    """One hypothesis of a test family and its externally supplied raw p-value."""

    hypothesis_id: str
    p_value: _Decimal

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.hypothesis_id, "hypothesis_id")
        _require_probability(self.p_value, "p_value")


@_dataclass(frozen=True, slots=True)
class HolmAdjustedHypothesis:
    """A hypothesis with its raw p-value, Holm-adjusted p-value and rejection decision."""

    hypothesis_id: str
    p_value: _Decimal
    adjusted_p_value: _Decimal
    rejected: bool

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.hypothesis_id, "hypothesis_id")
        _require_probability(self.p_value, "p_value")
        _require_probability(self.adjusted_p_value, "adjusted_p_value")
        if not isinstance(self.rejected, bool):
            raise TypeError(f"rejected must be a bool, got {type(self.rejected).__name__}")


@_dataclass(frozen=True, slots=True)
class HolmCorrectionResult:
    """Holm result for one family; `hypotheses` follows the input order exactly."""

    family_id: str
    significance_level: _Decimal
    hypotheses: tuple[HolmAdjustedHypothesis, ...]

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.family_id, "family_id")
        if not isinstance(self.significance_level, _Decimal):
            raise TypeError(
                "significance_level must be a Decimal, "
                f"got {type(self.significance_level).__name__}"
            )
        if not isinstance(self.hypotheses, tuple):
            raise TypeError(f"hypotheses must be a tuple, got {type(self.hypotheses).__name__}")
        for index, hypothesis in enumerate(self.hypotheses):
            if not isinstance(hypothesis, HolmAdjustedHypothesis):
                raise TypeError(
                    f"hypotheses[{index}] must be a HolmAdjustedHypothesis, "
                    f"got {type(hypothesis).__name__}"
                )


def _exact_multiple(p_value: _Decimal, multiplier: int) -> _Decimal:
    """`multiplier * p_value` exactly, without any Decimal context rounding."""
    _sign, digits, exponent = p_value.copy_abs().as_tuple()
    coefficient = int("".join(str(digit) for digit in digits)) * multiplier
    return _Decimal((0, tuple(int(c) for c in str(coefficient)), exponent))


def apply_holm_correction(
    family_id: str,
    hypotheses: tuple[HypothesisPValue, ...],
    *,
    significance_level: _Decimal,
) -> HolmCorrectionResult:
    """Holm-adjust the p-values of one declared family (Bölüm 17.6.3-17.6.6)."""
    _require_canonical_identifier(family_id, "family_id")  # 1-2
    if not isinstance(hypotheses, tuple):
        raise TypeError(f"hypotheses must be a tuple, got {type(hypotheses).__name__}")  # 3
    if len(hypotheses) == 0:
        raise ValueError("hypotheses must not be empty")  # 4
    for index, hypothesis in enumerate(hypotheses):  # 5
        if not isinstance(hypothesis, HypothesisPValue):
            raise TypeError(
                f"hypotheses[{index}] must be a HypothesisPValue, got {type(hypothesis).__name__}"
            )
    first_index: dict[str, int] = {}
    for index, hypothesis in enumerate(hypotheses):  # 6
        if hypothesis.hypothesis_id in first_index:
            raise ValueError(
                f"hypotheses[{index}].hypothesis_id {hypothesis.hypothesis_id!r} duplicates "
                f"hypotheses[{first_index[hypothesis.hypothesis_id]}].hypothesis_id"
            )
        first_index[hypothesis.hypothesis_id] = index
    if not isinstance(significance_level, _Decimal):
        raise TypeError(
            f"significance_level must be a Decimal, got {type(significance_level).__name__}"
        )  # 7
    if not significance_level.is_finite() or not (_Decimal(0) < significance_level < _Decimal(1)):
        raise ValueError(
            "significance_level must be finite and satisfy 0 < significance_level < 1, "
            f"got {significance_level}"
        )  # 8

    family_size = len(hypotheses)
    order = sorted(range(family_size), key=lambda index: (hypotheses[index].p_value, index))
    adjusted: list[_Decimal] = [_Decimal(0)] * family_size
    running_max: _Decimal | None = None
    for position, index in enumerate(order):
        product = _exact_multiple(hypotheses[index].p_value, family_size - position)
        running_max = product if running_max is None else max(running_max, product)
        adjusted[index] = min(running_max, _Decimal(1))

    return HolmCorrectionResult(
        family_id=family_id,
        significance_level=significance_level,
        hypotheses=tuple(
            HolmAdjustedHypothesis(
                hypothesis_id=hypothesis.hypothesis_id,
                p_value=hypothesis.p_value,
                adjusted_p_value=adjusted[index],
                rejected=adjusted[index] <= significance_level,
            )
            for index, hypothesis in enumerate(hypotheses)
        ),
    )
