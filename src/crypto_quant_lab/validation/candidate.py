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


def _require_str_type(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")


def _require_canonical_content(value: str, field_name: str) -> None:
    """Empty or whitespace-only, and leading/trailing whitespace padding, both -> ValueError.

    Case-sensitive; no Unicode normalization, no length limit (Bölüm 18.6).
    Assumes `value` has already passed `_require_str_type`.
    """
    stripped = value.strip()
    if stripped == "":
        raise ValueError(f"{field_name} must not be empty or whitespace-only")
    if value != stripped:
        raise ValueError(f"{field_name} must not have leading/trailing whitespace padding")


def _require_canonical_identifier(value: object, field_name: str) -> None:
    """Enforce the shared `candidate_id`/provenance-string rule: type, then content (Bölüm 18.6).

    Used for single-field checks (`candidate_id`, each `Trial` provenance
    field) where type and content are validated back-to-back for the SAME
    field. NOT used for `Candidate.parameters` keys, whose type (stage 5)
    and content (stage 6) must each be validated as a separate global pass
    over every entry before the next stage begins (Bölüm 18.8) — see
    `Candidate.__post_init__`.
    """
    _require_str_type(value, field_name)
    _require_canonical_content(value, field_name)


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
        """Enforce Bölüm 18.8's exact 9-stage global fail-fast order.

        Stages 1-3 are single-field checks (`candidate_id`, then the
        top-level `parameters` type). Stages 4-9 are each a SEPARATE global
        pass over every `parameters` entry, in this exact order: entry
        shape (4) -> key type (5) -> key content (6) -> duplicate keys (7)
        -> canonical ascending order (8) -> recursive value domain (9).
        Every entry must clear stage N (for every index) before stage N+1
        examines any entry — a later stage's violation at an earlier index
        always wins over an earlier stage's violation at a later index,
        never the reverse (Bölüm 18.8, corrected).
        """
        _require_canonical_identifier(self.candidate_id, "candidate_id")  # stages 1-2

        if not isinstance(self.parameters, tuple):
            raise TypeError(
                f"parameters must be a tuple, got {type(self.parameters).__name__}"
            )  # stage 3

        # Stage 4: entry shape -- global pass over every entry.
        for index, entry in enumerate(self.parameters):
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError(
                    f"parameters[{index}] must be a 2-tuple of (key, value), got {entry!r}"
                )

        # Stage 5: key type -- global pass, only reached once stage 4 clears every entry.
        for index, (key, _value) in enumerate(self.parameters):
            _require_str_type(key, f"parameters[{index}] key")

        # Stage 6: key content -- global pass, only reached once stage 5 clears every key.
        for index, (key, _value) in enumerate(self.parameters):
            _require_canonical_content(key, f"parameters[{index}] key")

        # Stage 7: duplicate keys -- global pass, only reached once stage 6 clears every key.
        seen_keys: set[str] = set()
        for index, (key, _value) in enumerate(self.parameters):
            if key in seen_keys:
                raise ValueError(f"parameters[{index}] key {key!r} is a duplicate key")
            seen_keys.add(key)

        # Stage 8: canonical ascending order -- global pass, only reached once stage 7 clears.
        previous_key: str | None = None
        for index, (key, _value) in enumerate(self.parameters):
            if previous_key is not None and key < previous_key:
                raise ValueError(
                    f"parameters[{index}] key {key!r} is out of canonical ascending order "
                    f"(previous key was {previous_key!r})"
                )
            previous_key = key

        # Stage 9: recursive value domain -- global pass, only reached once stages 3-8 clear.
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
