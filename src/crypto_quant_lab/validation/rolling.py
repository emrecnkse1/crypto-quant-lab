"""Rolling fixed-policy Layer-2 orchestrators (VALIDATION_SPEC.md Bölüm 8.3.6, 8.3.16, 13, 22, 23, 28.C, 28.F).

Two public orchestrators share one private execution mechanism
(`_execute_windows`):

- `run_rolling_backtest_from_store` — zero-context only: every window's
  `evaluation_start` equals its own `start` (VALIDATION_SPEC.md Bölüm
  8.3.1's legal "sıfır context" case) — non-zero per-window context/
  lookback is out of scope for this orchestrator.
- `run_context_aware_rolling_backtest_from_store` — non-zero-context:
  each window explicitly supplies its own `context_start` via
  `ContextAwareWindow` (VALIDATION_SPEC.md Bölüm 8.3.16), forwarded
  unchanged to the same canonical Layer-1 runner.

Both execute an ordered tuple of independent evaluation windows, each via
one direct `run_backtest_from_store` call (Bölüm 4, 21: compose, never
duplicate, canonical replay/accounting/execution). `TemporalSplit` (IS/OOS
research-selection boundary) is not accepted or repurposed by either
(Bölüm 8.3.12) — IS/train windows are neither executed nor recorded.

Both enforce the Bölüm 8.3.6 LOCKED policy-instance-freshness contract:
`policy_factory` is invoked exactly once per window, immediately before that
window executes, in input order; every returned instance is strongly
retained for the duration of orchestration and checked for cross-window
object-identity reuse (never equality); an invalid or reused instance fails
before any candle/funding I/O for the affected window. Sequential, fail-fast
— a failure at window N leaves window N and later windows unexecuted, does
not roll back earlier windows, and returns no partial result (Bölüm 8.3.6,
27: no parallel/distributed execution).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult
from crypto_quant_lab.backtest.policy import BacktestPolicy
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.storage.base import HistoricalCandleStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us
from crypto_quant_lab.validation.windows import TemporalWindow


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One evaluation window paired with its exact `BacktestResult` (VALIDATION_SPEC.md Bölüm 28.C).

    Pure pairing only — no metrics, no aggregate fields, no candidate
    identity, no mutable state, no custom ordering beyond ordinary dataclass
    value equality. Shared unchanged by both the zero-context and the
    context-aware orchestrator (Bölüm 8.3.16) — `window` is always a plain
    `TemporalWindow` (the evaluation window), never a `ContextAwareWindow`.
    """

    window: TemporalWindow
    result: BacktestResult

    def __post_init__(self) -> None:
        if not isinstance(self.window, TemporalWindow):
            raise TypeError(f"window must be a TemporalWindow, got {type(self.window).__name__}")
        if not isinstance(self.result, BacktestResult):
            raise TypeError(f"result must be a BacktestResult, got {type(self.result).__name__}")


@dataclass(frozen=True, slots=True)
class ContextAwareWindow:
    """An explicit per-window context boundary paired with its evaluation window (VALIDATION_SPEC.md Bölüm 8.3.16).

    Composes an existing `TemporalWindow` rather than repeating its fields
    — `TemporalWindow`/`TemporalSplit` remain unchanged (Bölüm 8.3.12, 21).
    `context_start == evaluation.start` is the legal zero-context special
    case (Bölüm 8.3.1); `context_start < evaluation.start` supplies genuine
    warm-up history. Grid-alignment is NOT checked here — `TemporalWindow`
    is timeframe-agnostic (Bölüm 6); that check remains Layer-1's own
    responsibility once a `timeframe` is known at call time (Bölüm 8.3.16).
    """

    context_start: datetime
    evaluation: TemporalWindow

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, TemporalWindow):
            raise TypeError(
                f"evaluation must be a TemporalWindow, got {type(self.evaluation).__name__}"
            )
        datetime_to_epoch_us(self.context_start)  # validates genuine, aware, non-pseudo-naive
        if self.context_start > self.evaluation.start:
            raise ValueError(
                "context_start must not be after evaluation.start, got "
                f"context_start={self.context_start!r}, evaluation.start={self.evaluation.start!r}"
            )


def _require_windows_tuple(windows: object) -> None:
    """Reject a non-tuple `windows` or any non-`TemporalWindow` element before any activity.

    Every element is checked up front — before the first window executes —
    so an invalid later element cannot cause partial execution. Does not
    duplicate `TemporalWindow`'s own datetime/boundary validation, which
    already ran at each instance's construction.
    """
    if not isinstance(windows, tuple):
        raise TypeError(f"windows must be a tuple, got {type(windows).__name__}")

    for index, window in enumerate(windows):
        if not isinstance(window, TemporalWindow):
            raise TypeError(
                f"windows[{index}] must be a TemporalWindow, got {type(window).__name__}"
            )


def _require_context_aware_windows_tuple(windows: object) -> None:
    """Reject a non-tuple `windows` or any non-`ContextAwareWindow` element before any activity.

    Every element is checked up front — before the first window executes —
    so an invalid later element cannot cause partial execution. Does not
    duplicate `ContextAwareWindow`'s own `context_start`/`evaluation`
    invariant validation, which already ran at each instance's construction
    (VALIDATION_SPEC.md Bölüm 8.3.16).
    """
    if not isinstance(windows, tuple):
        raise TypeError(f"windows must be a tuple, got {type(windows).__name__}")

    for index, window in enumerate(windows):
        if not isinstance(window, ContextAwareWindow):
            raise TypeError(
                f"windows[{index}] must be a ContextAwareWindow, got {type(window).__name__}"
            )


def _require_callable_policy_factory(policy_factory: object) -> None:
    if not callable(policy_factory):
        raise TypeError(f"policy_factory must be callable, got {type(policy_factory).__name__}")


def _require_valid_policy_result(policy: object, *, window_index: int) -> None:
    """Reject a `policy_factory` result that does not structurally provide `target_position`.

    A shape/duck-type check only (VALIDATION_SPEC.md Bölüm 8.3.6) — this
    never claims to prove semantic correctness, Type-H validity, or Type-I
    validity.
    """
    if not callable(getattr(policy, "target_position", None)):
        raise TypeError(
            f"policy_factory result for windows[{window_index}] must provide a callable "
            f"target_position, got {type(policy).__name__}"
        )


def _execute_windows(
    store: HistoricalCandleStore,
    boundaries: tuple[tuple[datetime, datetime, datetime, TemporalWindow], ...],
    *,
    policy_factory: Callable[[], BacktestPolicy],
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_required: bool,
    funding_store: HistoricalFundingStore | None,
    funding_model: FundingModel | None,
) -> tuple[WindowResult, ...]:
    """Shared per-window execution loop for both rolling orchestrators (VALIDATION_SPEC.md Bölüm 8.3.6, 8.3.16).

    `boundaries[i]` is a pre-resolved `(requested_start, requested_end,
    evaluation_start, result_window)` tuple — resolved by the caller from
    either a `TemporalWindow` (zero-context: `requested_start=window.start,
    requested_end=window.end, evaluation_start=window.start`) or a
    `ContextAwareWindow` (non-zero-context: `requested_start=context_start,
    requested_end=evaluation.end, evaluation_start=evaluation.start`).
    Resolving boundaries up front is pure data extraction (no I/O, no
    policy construction) — it does not make policy construction eager;
    `policy_factory()` is still called exactly once per window, immediately
    before that window's own `run_backtest_from_store` call, lazily inside
    this loop.

    For each window, in order: `policy_factory()` is called exactly once,
    immediately validated for a structurally callable `target_position`,
    then checked for object-identity reuse (`is`, never `==`) against every
    previously accepted, strongly retained instance (Bölüm 8.3.6 LOCKED —
    never reset()/clone()/copy()/deepcopy()) — both checks happen strictly
    before any candle/funding I/O for that window. A factory exception
    propagates completely unchanged. If window N fails validation/reuse/
    execution, window N and every later window do not execute; earlier
    windows may already have executed; no rollback is attempted and no
    partial result tuple is returned — the function raises.
    """
    seen_policies: list[BacktestPolicy] = []
    window_results: list[WindowResult] = []

    for index, (requested_start, requested_end, evaluation_start, result_window) in enumerate(
        boundaries
    ):
        policy = policy_factory()

        _require_valid_policy_result(policy, window_index=index)

        for previous_policy in seen_policies:
            if policy is previous_policy:
                raise ValueError(
                    f"policy_factory returned an already-used policy instance for windows[{index}]"
                )
        seen_policies.append(policy)

        result = run_backtest_from_store(
            store,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            timeframe=timeframe,
            requested_start=requested_start,
            requested_end=requested_end,
            as_of_time=as_of_time,
            config=config,
            policy=policy,
            cost_model=cost_model,
            funding_required=funding_required,
            funding_store=funding_store,
            funding_model=funding_model,
            evaluation_start=evaluation_start,
        )

        window_results.append(WindowResult(window=result_window, result=result))

    return tuple(window_results)


def run_rolling_backtest_from_store(
    store: HistoricalCandleStore,
    windows: tuple[TemporalWindow, ...],
    *,
    policy_factory: Callable[[], BacktestPolicy],
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_required: bool = False,
    funding_store: HistoricalFundingStore | None = None,
    funding_model: FundingModel | None = None,
) -> tuple[WindowResult, ...]:
    """Execute each of `windows` independently through canonical `run_backtest_from_store`.

    Zero-context only (VALIDATION_SPEC.md Bölüm 8.3.1): every window's
    `evaluation_start` is exactly its own `start` — `requested_start =
    window.start`, `requested_end = window.end`, `evaluation_start =
    window.start`. Windows execute in exact input order — no sorting,
    deduplication, clipping, normalization, or overlap rejection (Bölüm 6).
    `TemporalSplit`/IS windows are not accepted here (Bölüm 8.3.12).

    `windows` and `policy_factory` are validated up front, before any
    factory invocation or store I/O — see `_require_windows_tuple`/
    `_require_callable_policy_factory`. `run_backtest_from_store`'s own
    shared-argument validation (funding configuration, grid alignment,
    quality gates) is not duplicated here; it is reused per executed window.

    Policy-instance-freshness enforcement and the per-window execution loop
    are delegated to the private `_execute_windows` helper, shared
    byte-for-byte with `run_context_aware_rolling_backtest_from_store`
    (Bölüm 8.3.6, 8.3.16) — this function's own observable behavior is
    unchanged from before that helper existed.
    """
    _require_windows_tuple(windows)
    _require_callable_policy_factory(policy_factory)

    boundaries = tuple((window.start, window.end, window.start, window) for window in windows)

    return _execute_windows(
        store,
        boundaries,
        policy_factory=policy_factory,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        as_of_time=as_of_time,
        config=config,
        cost_model=cost_model,
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )


def run_context_aware_rolling_backtest_from_store(
    store: HistoricalCandleStore,
    windows: tuple[ContextAwareWindow, ...],
    *,
    policy_factory: Callable[[], BacktestPolicy],
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_required: bool = False,
    funding_store: HistoricalFundingStore | None = None,
    funding_model: FundingModel | None = None,
) -> tuple[WindowResult, ...]:
    """Execute each `ContextAwareWindow` independently through canonical `run_backtest_from_store` (VALIDATION_SPEC.md Bölüm 8.3.16).

    Non-zero-context counterpart of `run_rolling_backtest_from_store`: each
    window supplies its own explicit `context_start`, forwarded unchanged
    as `requested_start`, together with `evaluation_start =
    windows[i].evaluation.start` and `requested_end =
    windows[i].evaluation.end` — one canonical Layer-1 call per window, no
    second candle/funding request, no replay duplication. `WindowResult.window`
    is `windows[i].evaluation` (a plain `TemporalWindow`), never the full
    `ContextAwareWindow` — `WindowResult` itself remains unchanged (Bölüm
    21); `context_start` remains auditable only via the caller's own
    original `windows` input, correlated by the preserved input order.

    `windows` and `policy_factory` are validated up front, before any
    factory invocation or store I/O — see
    `_require_context_aware_windows_tuple`/`_require_callable_policy_factory`.
    `ContextAwareWindow`'s own `context_start`/`evaluation` invariants are
    not re-validated here, since they already ran at each instance's
    construction. Windows execute in exact input order — no sorting,
    deduplication, clipping, normalization, or overlap rejection, for
    either the evaluation or the context interval (Bölüm 8.3.16).

    Policy-instance-freshness enforcement and the per-window execution loop
    are delegated to the private `_execute_windows` helper, shared
    byte-for-byte with `run_rolling_backtest_from_store` (Bölüm 8.3.6,
    8.3.16).
    """
    _require_context_aware_windows_tuple(windows)
    _require_callable_policy_factory(policy_factory)

    boundaries = tuple(
        (window.context_start, window.evaluation.end, window.evaluation.start, window.evaluation)
        for window in windows
    )

    return _execute_windows(
        store,
        boundaries,
        policy_factory=policy_factory,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        as_of_time=as_of_time,
        config=config,
        cost_model=cost_model,
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )
