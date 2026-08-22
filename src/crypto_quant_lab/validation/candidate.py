"""Candidate/Trial foundation value objects (VALIDATION_SPEC.md Bölüm 18, 28.G).

Pure, immutable value objects only — no evaluator function, no policy
builder/factory, no live policy instance, no callable field, no
selection/test role, no score/rank/aggregate/winner field, no optimizer or
search. `Candidate` is an explicit identifier plus immutable parameter
metadata; `Trial` is a candidate paired with the ordered, fully-successful
multi-window evidence (`tuple[WindowResult, ...]`) that evaluated it, plus
provenance not otherwise recoverable from `BacktestResult`/`WindowResult`.

This foundation, on its own, provides NO train/select/test split, NO best-
candidate selection, NO final-holdout protection, and NO multiple-testing
correction (Bölüm 18.9) — those remain the responsibility of a future,
separate orchestration contract.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us
from crypto_quant_lab.validation.rolling import WindowResult

ParameterValue = bool | int | Decimal | str | None | tuple["ParameterValue", ...]


def _require_canonical_identifier(value: object, field_name: str) -> None:
    """Enforce the shared `candidate_id`/parameter-key/provenance-string rule.

    Non-str -> TypeError. Empty or whitespace-only, and leading/trailing
    whitespace padding, both -> ValueError. Case-sensitive; no Unicode
    normalization, no length limit (VALIDATION_SPEC.md Bölüm 18.6).
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    stripped = value.strip()
    if stripped == "":
        raise ValueError(f"{field_name} must not be empty or whitespace-only")
    if value != stripped:
        raise ValueError(f"{field_name} must not have leading/trailing whitespace padding")


def _require_parameter_value(value: object, *, top_level_index: int) -> None:
    """Validate one parameter value against the locked recursive domain (Bölüm 18.6).

    Order: bool (before int) -> int (non-bool) -> Decimal (finite only) ->
    str (unrestricted content) -> None -> tuple (recursive, same domain) ->
    float REJECTED -> mutable containers REJECTED -> any other object
    REJECTED. Nested failures report the top-level `parameters` index, never
    a nested index, consistently with Bölüm 18.6.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(
                f"parameters[{top_level_index}] value must be a finite Decimal, got {value!r}"
            )
        return
    if isinstance(value, str):
        return
    if value is None:
        return
    if isinstance(value, tuple):
        for element in value:
            _require_parameter_value(element, top_level_index=top_level_index)
        return
    if isinstance(value, float):
        raise TypeError(f"parameters[{top_level_index}] value must not be a float, got {value!r}")
    raise TypeError(
        f"parameters[{top_level_index}] value has unsupported type {type(value).__name__}"
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A pure, immutable candidate definition (VALIDATION_SPEC.md Bölüm 18.4, 18.5).

    An explicit identifier plus immutable parameter metadata — NOT a policy
    factory/callable, NOT a concrete policy instance, NOT a trial, NOT a
    score/rank. `parameters` keys must be supplied in strict ascending
    lexicographic order (Bölüm 18.6) — non-canonical order is rejected, never
    silently sorted.
    """

    candidate_id: str
    parameters: tuple[tuple[str, ParameterValue], ...]

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.candidate_id, "candidate_id")

        if not isinstance(self.parameters, tuple):
            raise TypeError(f"parameters must be a tuple, got {type(self.parameters).__name__}")

        previous_key: str | None = None
        for index, entry in enumerate(self.parameters):
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError(
                    f"parameters[{index}] must be a 2-tuple of (key, value), got {entry!r}"
                )
            key, _value = entry
            _require_canonical_identifier(key, f"parameters[{index}] key")

            if previous_key is not None:
                if key == previous_key:
                    raise ValueError(f"parameters[{index}] key {key!r} is a duplicate key")
                if key < previous_key:
                    raise ValueError(
                        f"parameters[{index}] key {key!r} is out of canonical ascending order "
                        f"(previous key was {previous_key!r})"
                    )
            previous_key = key

        for index, (_key, value) in enumerate(self.parameters):
            _require_parameter_value(value, top_level_index=index)


@dataclass(frozen=True, slots=True)
class Trial:
    """A candidate's ordered, fully-successful multi-window evaluation evidence (Bölüm 18.4, 18.7).

    `results` is the raw `tuple[WindowResult, ...]` evidence — metrics are
    derived externally (Stage-1/Stage-2, unchanged) from
    `trial.results[i].result`, never stored here. Provenance fields
    (`exchange`/`market_type`/`symbol`/`timeframe`/`as_of_time`/`config`) are
    stored separately because they are NOT recoverable from `BacktestResult`
    (Bölüm 18.1, 18.7). Carries NO selection/test role field (Bölüm 18.7 —
    KRİTİK: adding one would falsely imply engine-enforced leakage safety,
    which this abstraction never provides, Bölüm 18.9, 19).
    """

    candidate: Candidate
    results: tuple[WindowResult, ...]
    exchange: str
    market_type: str
    symbol: str
    timeframe: str
    as_of_time: datetime
    config: BacktestConfig

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, Candidate):
            raise TypeError(f"candidate must be a Candidate, got {type(self.candidate).__name__}")

        if not isinstance(self.results, tuple):
            raise TypeError(f"results must be a tuple, got {type(self.results).__name__}")
        if len(self.results) == 0:
            raise ValueError("results must not be empty")
        for index, result in enumerate(self.results):
            if not isinstance(result, WindowResult):
                raise TypeError(
                    f"results[{index}] must be a WindowResult, got {type(result).__name__}"
                )

        for field_name, value in (
            ("exchange", self.exchange),
            ("market_type", self.market_type),
            ("symbol", self.symbol),
            ("timeframe", self.timeframe),
        ):
            _require_canonical_identifier(value, field_name)

        datetime_to_epoch_us(self.as_of_time)  # validates genuine, aware, non-pseudo-naive

        if not isinstance(self.config, BacktestConfig):
            raise TypeError(f"config must be a BacktestConfig, got {type(self.config).__name__}")

        for index, window_result in enumerate(self.results):
            if window_result.result.initial_cash != self.config.initial_cash:
                raise ValueError(
                    f"results[{index}].result.initial_cash "
                    f"({window_result.result.initial_cash!r}) does not match "
                    f"config.initial_cash ({self.config.initial_cash!r})"
                )
