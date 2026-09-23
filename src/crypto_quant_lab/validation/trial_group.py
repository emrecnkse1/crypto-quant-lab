"""Trial-group / recorded-trial-count foundation (VALIDATION_SPEC.md Bölüm 20.1-20.13, 28.J).

Pure, immutable value object plus one counting function — no metric
computation, no Deflated Sharpe, no effective/independent trial count, no
selection/ranking/score, no persistence, no registry. `TrialGroup` is one
comparable group of successful `Trial`s: unique `candidate_id`s, identical
provenance and identical ordered evaluation windows. `recorded_trial_count`
is the exact number of `Trial` records accepted into that group — it does
NOT prove the total number of research attempts, the number of distinct
strategies, or an effective trial count, and gives no unconditional lower or
upper bound on real research attempts (Bölüm 20.7). This foundation provides
NO final-holdout protection and no selection/test role (Bölüm 18.7, 19).
"""

from dataclasses import dataclass as _dataclass

from crypto_quant_lab.validation.candidate import Trial as _Trial

_PROVENANCE_FIELDS = ("exchange", "market_type", "symbol", "timeframe", "as_of_time", "config")


def _require_group_id(value: object) -> None:
    """Same rule and message pattern as `candidate_id` (Bölüm 18.6), redefined locally.

    `candidate.py`'s `_` prefixed helpers are private and are not imported
    across modules (Bölüm 20.8).
    """
    if not isinstance(value, str):
        raise TypeError(f"group_id must be a str, got {type(value).__name__}")
    stripped = value.strip()
    if stripped == "":
        raise ValueError("group_id must not be empty or whitespace-only")
    if value != stripped:
        raise ValueError("group_id must not have leading/trailing whitespace padding")


def _evaluation_windows(trial: _Trial) -> tuple:
    return tuple(window_result.window for window_result in trial.results)


@_dataclass(frozen=True, slots=True)
class TrialGroup:
    """One comparable group of successful trials (VALIDATION_SPEC.md Bölüm 20.4-20.6).

    `trials` is kept exactly as given — never sorted, filtered, deduplicated
    or copied. Order carries no ranking meaning; equality is order-sensitive
    value semantics only.
    """

    group_id: str
    trials: tuple[_Trial, ...]

    def __post_init__(self) -> None:
        """Enforce Bölüm 20.8's exact 13-stage global fail-fast order.

        Every multi-element stage is a SEPARATE global pass over `trials`:
        stage N must clear for every index before stage N+1 examines any
        index. Stages 7-12 are one pass per provenance field, in the locked
        field order, each comparing indices 1.. against `trials[0]`.
        """
        _require_group_id(self.group_id)  # stages 1-2

        if not isinstance(self.trials, tuple):
            raise TypeError(f"trials must be a tuple, got {type(self.trials).__name__}")  # stage 3
        if len(self.trials) == 0:
            raise ValueError("trials must not be empty")  # stage 4

        # Stage 5: element type -- global pass.
        for index, trial in enumerate(self.trials):
            if not isinstance(trial, _Trial):
                raise TypeError(f"trials[{index}] must be a Trial, got {type(trial).__name__}")

        # Stage 6: candidate_id uniqueness -- global pass.
        first_index_by_candidate_id: dict[str, int] = {}
        for index, trial in enumerate(self.trials):
            candidate_id = trial.candidate.candidate_id
            if candidate_id in first_index_by_candidate_id:
                raise ValueError(
                    f"trials[{index}].candidate.candidate_id {candidate_id!r} duplicates "
                    f"trials[{first_index_by_candidate_id[candidate_id]}].candidate.candidate_id"
                )
            first_index_by_candidate_id[candidate_id] = index

        reference = self.trials[0]

        # Stages 7-12: provenance homogeneity -- one global pass per field.
        for field_name in _PROVENANCE_FIELDS:
            reference_value = getattr(reference, field_name)
            for index in range(1, len(self.trials)):
                value = getattr(self.trials[index], field_name)
                if value != reference_value:
                    raise ValueError(
                        f"trials[{index}].{field_name} ({value!r}) does not match "
                        f"trials[0].{field_name} ({reference_value!r})"
                    )

        # Stage 13: ordered evaluation window sequence -- global pass.
        reference_windows = _evaluation_windows(reference)
        for index in range(1, len(self.trials)):
            if _evaluation_windows(self.trials[index]) != reference_windows:
                raise ValueError(
                    f"trials[{index}] evaluation windows do not match trials[0] evaluation windows"
                )


def recorded_trial_count(group: TrialGroup) -> int:
    """Exact number of `Trial` records accepted into `group` (Bölüm 20.7).

    Not a count of all research attempts, not a count of distinct strategies,
    and not an effective/independent trial count.
    """
    if not isinstance(group, TrialGroup):
        raise TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
    return len(group.trials)
