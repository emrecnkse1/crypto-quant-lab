"""Descriptive parameter-neighborhood map of a TrialGroup (VALIDATION_SPEC.md Bölüm 17.7.1-17.7.8, 28.T).

The prerequisite §17.7 lists as missing — "neighboring parameter
configurations" — is taken ONLY from what was actually evaluated: the
`Candidate.parameters` of the trials in one `TrialGroup`. Nothing is
generated, searched or optimized.

- Every candidate must declare the same parameter keys; two candidates with
  identical parameters are refused (ambiguous grid point).
- A key whose values are all int (not bool) or finite Decimal is an ordered
  axis; any other key (str, bool, None, tuple) is categorical and must match
  exactly between neighbors.
- The lower/upper neighbor of candidate c along axis k is the candidate whose
  other keys all equal c's and whose k is the nearest smaller/larger value
  present in the group.
- Each candidate is described by its unchanged Stage-2 per-observation Sharpe
  ratio (single-window trials only, as for DSR and the Sharpe p-values).

Descriptive only: no stability score, tolerance, plateau criterion, ranking
or selection — the stability METRIC is an open decision (§17.7.8).
"""

from dataclasses import dataclass as _dataclass
from decimal import Decimal as _Decimal

from crypto_quant_lab.validation.metrics import compute_stage2_metrics as _compute_stage2_metrics
from crypto_quant_lab.validation.trial_group import TrialGroup as _TrialGroup

MAP_SCOPE = (
    "descriptive neighborhood map over the parameters actually evaluated in one TrialGroup; "
    "no stability score, tolerance, plateau criterion, ranking or selection"
)


@_dataclass(frozen=True, slots=True)
class ParameterNeighbor:
    parameter: str
    direction: str  # "lower" or "upper"
    candidate_id: str
    value: int | _Decimal
    sharpe_ratio: _Decimal

    def __post_init__(self) -> None:
        if self.direction not in ("lower", "upper"):
            raise ValueError(f"direction must be 'lower' or 'upper', got {self.direction!r}")


@_dataclass(frozen=True, slots=True)
class CandidateNeighborhood:
    candidate_id: str
    parameters: tuple
    sharpe_ratio: _Decimal
    neighbors: tuple[ParameterNeighbor, ...]


@_dataclass(frozen=True, slots=True)
class ParameterNeighborhoodMap:
    scope: str
    ordered_parameters: tuple[str, ...]
    categorical_parameters: tuple[str, ...]
    candidates: tuple[CandidateNeighborhood, ...]


def _is_ordered(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, _Decimal) and value.is_finite())


def map_parameter_neighborhoods(
    group: _TrialGroup, *, risk_free_per_period: _Decimal = _Decimal(0)
) -> ParameterNeighborhoodMap:
    """Neighborhood map of `group` (Bölüm 17.7.3-17.7.6)."""
    if not isinstance(group, _TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
    if not isinstance(risk_free_per_period, _Decimal):
        raise TypeError(
            f"risk_free_per_period must be a Decimal, got {type(risk_free_per_period).__name__}"
        )
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")
    trials = group.trials
    if len(trials) < 2:
        raise ValueError(f"at least two candidates are required, got {len(trials)}")
    keys = tuple(key for key, _ in trials[0].candidate.parameters)
    for index, trial in enumerate(trials):
        own = tuple(key for key, _ in trial.candidate.parameters)
        if own != keys:
            raise ValueError(
                f"trials[{index}] parameter keys {list(own)} differ from trials[0] {list(keys)}; "
                "all candidates must share one parameter key set"
            )
        if len(trial.results) != 1:
            raise ValueError(
                f"trials[{index}] must have exactly one window result, got {len(trial.results)} "
                "(multi-window pooling is not defined)"
            )
    values = [dict(trial.candidate.parameters) for trial in trials]
    seen: dict[tuple, int] = {}
    for index, trial in enumerate(trials):
        point = trial.candidate.parameters
        if point in seen:
            raise ValueError(
                f"trials[{index}] repeats the parameters of trials[{seen[point]}]; a grid point "
                "must be evaluated once"
            )
        seen[point] = index
    ordered = tuple(k for k in keys if all(_is_ordered(v[k]) for v in values))
    categorical = tuple(k for k in keys if k not in ordered)
    if not ordered:
        raise ValueError("no ordered (int or Decimal) parameter: a neighborhood is undefined")

    sharpes = [
        _compute_stage2_metrics(
            trial.results[0].result, risk_free_per_period=risk_free_per_period
        ).sharpe_ratio
        for trial in trials
    ]
    candidates = []
    for index, trial in enumerate(trials):
        neighbors = []
        for key in ordered:
            others = [k for k in keys if k != key]
            peers = [
                j
                for j in range(len(trials))
                if j != index and all(values[j][k] == values[index][k] for k in others)
            ]
            own = values[index][key]
            below = [j for j in peers if values[j][key] < own]
            above = [j for j in peers if values[j][key] > own]
            for direction, pool, pick in (("lower", below, max), ("upper", above, min)):
                if pool:
                    j = pick(pool, key=lambda p, key=key: values[p][key])
                    neighbors.append(
                        ParameterNeighbor(
                            parameter=key,
                            direction=direction,
                            candidate_id=trials[j].candidate.candidate_id,
                            value=values[j][key],
                            sharpe_ratio=sharpes[j],
                        )
                    )
        candidates.append(
            CandidateNeighborhood(
                candidate_id=trial.candidate.candidate_id,
                parameters=trial.candidate.parameters,
                sharpe_ratio=sharpes[index],
                neighbors=tuple(neighbors),
            )
        )
    return ParameterNeighborhoodMap(
        scope=MAP_SCOPE,
        ordered_parameters=ordered,
        categorical_parameters=categorical,
        candidates=tuple(candidates),
    )
