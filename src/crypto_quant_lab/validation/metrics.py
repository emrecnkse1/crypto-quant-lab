"""Stage-1 metrics foundation: total return + maximum drawdown (VALIDATION_SPEC.md Bölüm 15.1-15.8, 28.D).

Pure, additive, `BacktestResult`-only value derivation — no metrics field is
attached to `BacktestResult` or `WindowResult` (both remain unchanged), no
store/replay/rolling-orchestration dependency, no cross-window aggregation.
The same `compute_stage1_metrics` works identically on a directly-produced
`BacktestResult` or on an independent `WindowResult.result`.

Only total return and maximum drawdown are in scope here (Bölüm 15.1) —
periodic return series, Sharpe/Sortino/Calmar, and every later-stage/advanced
metric remain out of scope and are never computed or approximated by this
module.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from crypto_quant_lab.backtest.models import BacktestResult, EquityPoint
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Stage1Metrics:
    """Total return + maximum drawdown for one `BacktestResult` (VALIDATION_SPEC.md Bölüm 15.3).

    A non-negative relative magnitude for `max_drawdown` (never signed, never
    an absolute currency amount) and a `Decimal` fraction for `total_return`
    (e.g. `Decimal("0.05")` == +5%) — neither field has an artificial upper
    bound, and `max_drawdown` additionally has no artificial lower bound
    beyond zero.
    """

    total_return: Decimal
    max_drawdown: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.total_return, "total_return")
        if not self.total_return.is_finite():
            raise ValueError(f"total_return must be finite, got {self.total_return}")

        _require_decimal(self.max_drawdown, "max_drawdown")
        if not self.max_drawdown.is_finite():
            raise ValueError(f"max_drawdown must be finite, got {self.max_drawdown}")
        if self.max_drawdown < Decimal(0):
            raise ValueError(f"max_drawdown must be >= 0, got {self.max_drawdown}")


def _stage1_decimal_context() -> Context:
    """A fresh, explicit computation context — never the caller's ambient one.

    A new `Context` is constructed per call (not a shared module-level
    constant) so external mutation of a would-be shared instance cannot
    alter Stage-1 arithmetic (VALIDATION_SPEC.md Bölüm 15.7). `traps=[]`
    means a fault (e.g. division by a zero/near-zero magnitude) produces a
    non-finite Decimal value rather than raising — the explicit
    post-computation finiteness checks below are what turn that into a
    deterministic `ValueError`, never a silently-returned non-finite metric.
    """
    return Context(
        prec=28,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        traps=[],
    )


def _require_backtest_result(result: object) -> None:
    if not isinstance(result, BacktestResult):
        raise TypeError(f"result must be a BacktestResult, got {type(result).__name__}")


def _require_valid_equity_curve(result: BacktestResult) -> None:
    """Validate `result.equity_curve` up front — before any arithmetic (Bölüm 15.4).

    Maximum drawdown is path-dependent and cannot be stated honestly
    without at least one equity observation, so an empty curve is rejected
    rather than silently treated as a zero-drawdown result. Curve order
    affects drawdown, so a directly-constructed, unordered/inconsistent
    `BacktestResult` (Bölüm 15.4's own rationale: canonical replay already
    guarantees these properties via `build_backtest_result`, so this is a
    public metrics-boundary defense, not a replay change) is not silently
    accepted.
    """
    if not result.equity_curve:
        raise ValueError("result.equity_curve must not be empty")

    previous_us: int | None = None
    for index, point in enumerate(result.equity_curve):
        if not isinstance(point, EquityPoint):
            raise TypeError(
                f"result.equity_curve[{index}] must be an EquityPoint, got {type(point).__name__}"
            )
        if not point.equity.is_finite():
            raise ValueError(
                f"result.equity_curve[{index}].equity must be finite, got {point.equity}"
            )

        current_us = datetime_to_epoch_us(point.time)
        if previous_us is not None and current_us <= previous_us:
            raise ValueError("result.equity_curve timestamps must be strictly ascending")
        previous_us = current_us

    if result.equity_curve[-1].equity != result.final_equity:
        raise ValueError(
            "result.equity_curve[-1].equity must equal result.final_equity: "
            f"{result.equity_curve[-1].equity!r} != {result.final_equity!r}"
        )


def compute_stage1_metrics(result: BacktestResult) -> Stage1Metrics:
    """Compute total return and maximum drawdown for `result` (VALIDATION_SPEC.md Bölüm 15).

    `result` must be an actual `BacktestResult` (concrete-type `isinstance`,
    the repository's existing convention — not a structural/duck-type
    acceptance). Validation runs to completion, in the exact locked order,
    strictly before any arithmetic: result type, `initial_cash`
    finite-and-positive, `final_equity` finite, then the whole equity curve
    (non-empty, every element an `EquityPoint`, every equity finite,
    strictly-ascending timestamps, last point consistent with
    `final_equity`). Nothing is silently sorted, filtered, clipped,
    normalized, or repaired.

    All arithmetic — total return's division/subtraction and maximum
    drawdown's single-pass peak-tracking loop (comparisons, subtraction,
    division) — runs inside a private, explicitly-constructed Decimal
    context (Bölüm 15.7), independent of the caller's ambient
    `decimal.getcontext()`. `total_return = final_equity / initial_cash -
    Decimal("1")` in exactly that operation order — never rewritten as
    `(final_equity - initial_cash) / initial_cash`, which can differ in its
    final digit under finite precision. `max_drawdown` seeds its running
    peak from `initial_cash` (not from `equity_curve[0]`, which the curve
    itself need not equal) so an immediate first-point loss is never
    hidden; it requires no recovery, is never capped at `1`, and is not a
    signed or absolute-currency value.
    """
    _require_backtest_result(result)

    if not result.initial_cash.is_finite():
        raise ValueError(f"result.initial_cash must be finite, got {result.initial_cash}")
    if result.initial_cash <= Decimal(0):
        raise ValueError(
            f"result.initial_cash must be greater than zero, got {result.initial_cash}"
        )
    if not result.final_equity.is_finite():
        raise ValueError(f"result.final_equity must be finite, got {result.final_equity}")

    _require_valid_equity_curve(result)

    with localcontext(_stage1_decimal_context()):
        total_return = result.final_equity / result.initial_cash - Decimal(1)

        peak = result.initial_cash
        max_drawdown = Decimal(0)
        for point in result.equity_curve:
            if point.equity > peak:
                peak = point.equity
            else:
                drawdown = (peak - point.equity) / peak
                max_drawdown = max(max_drawdown, drawdown)

    if not total_return.is_finite():
        raise ValueError(f"computed total_return must be finite, got {total_return}")
    if not max_drawdown.is_finite():
        raise ValueError(f"computed max_drawdown must be finite, got {max_drawdown}")

    return Stage1Metrics(total_return=total_return, max_drawdown=max_drawdown)
