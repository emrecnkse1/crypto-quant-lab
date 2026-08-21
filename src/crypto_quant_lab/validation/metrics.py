"""Stage-1 (VALIDATION_SPEC.md Bölüm 15.1-15.8, 28.D) and Stage-2
(Bölüm 15.9-15.18, 28.E) metrics foundation.

Pure, additive, `BacktestResult`-only value derivation — no metrics field is
attached to `BacktestResult` or `WindowResult` (both remain unchanged), no
store/replay/rolling-orchestration dependency, no cross-window aggregation.
Every public function here works identically on a directly-produced
`BacktestResult` or on an independent `WindowResult.result`.

Stage-1: total return + maximum drawdown (Bölüm 15.1).
Stage-2: periodic/simple return-series + arithmetic mean + sample standard
deviation + non-annualized, per-observation Sharpe (Bölüm 15.9). Both stages
consume `result.equity_curve` exactly as canonical replay produces it — no
second evaluation-boundary filter is applied here, since context candles
already never produce an `EquityPoint` (Bölüm 15.10; `backtest/replay.py`'s
main loop `continue`s before ever appending one).

Annualized Sharpe, Sortino, Calmar, CAGR, and every later-stage/advanced
metric remain out of scope and are never computed or approximated by this
module (Bölüm 15.9, 27).
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


@dataclass(frozen=True, slots=True)
class Stage2Metrics:
    """Mean/stdev/Sharpe over one `BacktestResult`'s return series (VALIDATION_SPEC.md Bölüm 15.9-15.18).

    `sharpe_ratio` is a non-annualized, per-observation, dimensionless
    ratio — never an annualized statistic. `return_stdev` is a non-negative
    sample standard deviation (`n - 1` denominator); this value object alone
    accepts `return_stdev == Decimal(0)` (only `compute_stage2_metrics`
    rejects it, because Sharpe would be undefined at that point). Neither
    field carries an artificial upper bound.
    """

    mean_return: Decimal
    return_stdev: Decimal
    sharpe_ratio: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.mean_return, "mean_return")
        if not self.mean_return.is_finite():
            raise ValueError(f"mean_return must be finite, got {self.mean_return}")

        _require_decimal(self.return_stdev, "return_stdev")
        if not self.return_stdev.is_finite():
            raise ValueError(f"return_stdev must be finite, got {self.return_stdev}")
        if self.return_stdev < Decimal(0):
            raise ValueError(f"return_stdev must be >= 0, got {self.return_stdev}")

        _require_decimal(self.sharpe_ratio, "sharpe_ratio")
        if not self.sharpe_ratio.is_finite():
            raise ValueError(f"sharpe_ratio must be finite, got {self.sharpe_ratio}")


def _metrics_decimal_context() -> Context:
    """A fresh, explicit computation context — never the caller's ambient one.

    A new `Context` is constructed per call (not a shared module-level
    constant) so external mutation of a would-be shared instance cannot
    alter Stage-1/Stage-2 arithmetic (VALIDATION_SPEC.md Bölüm 15.7, 15.17).
    `traps=[]` means a fault (e.g. division by a zero/near-zero magnitude, or
    `sqrt` of a negative value) produces a non-finite Decimal value rather
    than raising — the explicit post-computation finiteness checks in each
    public function are what turn that into a deterministic `ValueError`,
    never a silently-returned non-finite metric. Shared identically by both
    stages (Bölüm 15.17: "Stage-2, Stage-1 için zaten kilitli olan AYNI
    explicit context shape'i kullanır").
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
    accepted. Reused unchanged by Stage-2 (Bölüm 15.15 steps 5-9 are
    identical to Stage-1's own steps 5-9).
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


def _require_core_result_contract(result: object) -> None:
    """Steps 1-9 of the locked validation order, shared by Stage-1 and Stage-2.

    `result` type, `initial_cash` finite-and-positive, `final_equity`
    finite, then the whole equity curve (Bölüm 15.4 / 15.15 steps 1-9 are
    identical between the two stages). Nothing is silently sorted,
    filtered, clipped, normalized, or repaired.
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


def _require_positive_denominators(result: BacktestResult) -> None:
    """Step 10 (Bölüm 15.15): every equity that will serve as a LATER return's denominator is > 0.

    Return index 0's denominator is `result.initial_cash`, already validated
    positive by `_require_core_result_contract`. Return index `i >= 1`'s
    denominator is `equity_curve[i - 1].equity` — so every curve equity
    *except the last* must be strictly positive. The terminal point never
    serves as a denominator (there is no return past the last observation),
    so it may legally be zero or negative (Bölüm 15.13).
    """
    curve = result.equity_curve
    for index in range(len(curve) - 1):
        equity = curve[index].equity
        if equity <= Decimal(0):
            raise ValueError(
                f"result.equity_curve[{index}].equity must be greater than zero to compute "
                f"return index {index + 1}, got {equity}"
            )


def _compute_periodic_returns_unchecked(result: BacktestResult) -> tuple[Decimal, ...]:
    """Arithmetic only — caller must already have run the full validation contract.

    `result.initial_cash` seeds the implicit "period 0" observation
    (Bölüm 15.13's peak-seed-style baseline); each subsequent return's
    denominator is the previous curve point's equity. Exact operation order
    is division first, subtraction second, matching Stage-1's total-return
    order (Bölüm 15.5, 15.13) — never rewritten as
    `(current - previous) / previous`.
    """
    with localcontext(_metrics_decimal_context()):
        returns: list[Decimal] = []
        previous_equity = result.initial_cash
        for point in result.equity_curve:
            returns.append(point.equity / previous_equity - Decimal(1))
            previous_equity = point.equity

    return tuple(returns)


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
    _require_core_result_contract(result)

    with localcontext(_metrics_decimal_context()):
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


def compute_periodic_returns(result: BacktestResult) -> tuple[Decimal, ...]:
    """Derive the per-observation simple-return series from `result.equity_curve` (Bölüm 15.13).

    Consumes only `result.equity_curve` and `result.initial_cash` — cash,
    fills, PnL, fees, cost, funding, and unrealized mark-to-market are never
    independently recomputed (they are already reflected in each curve
    equity). `result.equity_curve` is already evaluation-only for both
    zero-context and context-aware Layer-1 results (Bölüm 15.10) — context
    candles never append an `EquityPoint`, so no second evaluation-boundary
    filter is applied here, and no `evaluation_start` argument exists on
    this function.

    For `N` equity points this returns exactly `N` returns (never `N - 1`):
    `initial_cash` is the implicit observation immediately before the first
    point. Simple returns only — never log returns (Bölüm 15.13's
    reconciliation with Stage-1's own legal negative/zero-equity paths).
    Current equity zero or negative is legal and can produce
    `Decimal("-1")` or a return below it; a *denominator* equity (every
    curve point except the last) that is zero or negative instead fails the
    whole call deterministically, before any division, with an index-
    specific `ValueError` naming the offending curve index and the return
    index it would have fed (Bölüm 15.15 step 10) — the series is never
    silently truncated at that point.

    Runs inside the same private Decimal context as Stage-1 (Bölüm 15.17).
    Every computed return is checked for finiteness after the arithmetic
    block; a computed NaN/Infinity is rejected with a deterministic, index-
    specific `ValueError` rather than being silently included in the
    returned tuple.
    """
    _require_core_result_contract(result)
    _require_positive_denominators(result)

    returns = _compute_periodic_returns_unchecked(result)

    for index, periodic_return in enumerate(returns):
        if not periodic_return.is_finite():
            raise ValueError(
                f"computed periodic return at index {index} must be finite, got {periodic_return}"
            )

    return returns


def compute_stage2_metrics(
    result: BacktestResult,
    *,
    risk_free_per_period: Decimal = Decimal(0),
) -> Stage2Metrics:
    """Compute mean/sample-stdev/non-annualized Sharpe for `result` (VALIDATION_SPEC.md Bölüm 15.9-15.18).

    `risk_free_per_period` is a per-OBSERVATION input (one "period" is one
    adjacent eligible equity-observation interval) — never an annual rate,
    and never converted from one (Bölüm 15.11, 15.14).

    Deterministic validation, strictly before any arithmetic: the full
    Stage-1-shared core contract (result type, `initial_cash` finite-and-
    positive, `final_equity` finite, whole equity curve), then every
    later-return denominator's positivity, then `risk_free_per_period`
    type/finiteness, then a minimum-two-return sample-count check. Because
    `N` equity points always produce exactly `N` returns (Bölüm 15.13), the
    sample-count check is a structural check on `len(result.equity_curve)`
    — it never requires running return arithmetic first, so the locked
    "validate before arithmetic" order holds without needing to call the
    public `compute_periodic_returns` (which would re-run the same
    validation a second time). Only after every check passes does return/
    statistical arithmetic run, entirely inside the same private Decimal
    context as Stage-1 (Bölüm 15.17): arithmetic mean (`sum(returns,
    Decimal(0)) / Decimal(n)`), sample variance with an `n - 1` denominator
    (never population variance), `return_stdev = sample_variance.sqrt()`,
    and finally `sharpe_ratio = (mean_return - risk_free_per_period) /
    return_stdev` — subtraction first, division second — computed only
    after `return_stdev` is confirmed strictly greater than zero (a zero or
    negative standard deviation makes Sharpe undefined and fails
    deterministically, never returning `None`/NaN/Infinity/an artificial
    zero). Every intermediate aggregate (`return_sum`, `mean_return`,
    `squared_deviation_sum`, `sample_variance`, `return_stdev`,
    `sharpe_ratio`) is checked for finiteness — a fault anywhere in that
    chain (e.g. extreme-magnitude equity overflowing the locked context)
    is rejected with a deterministic `ValueError`, never silently returned.
    """
    _require_core_result_contract(result)
    _require_positive_denominators(result)

    _require_decimal(risk_free_per_period, "risk_free_per_period")
    if not risk_free_per_period.is_finite():
        raise ValueError(f"risk_free_per_period must be finite, got {risk_free_per_period}")

    return_count = len(result.equity_curve)
    if return_count < 2:
        raise ValueError(
            "at least two periodic returns are required to compute Stage-2 statistics, "
            f"got {return_count}"
        )

    returns = _compute_periodic_returns_unchecked(result)

    with localcontext(_metrics_decimal_context()):
        n = len(returns)
        return_sum = sum(returns, Decimal(0))
        mean_return = return_sum / Decimal(n)

        squared_deviation_sum = Decimal(0)
        for periodic_return in returns:
            deviation = periodic_return - mean_return
            squared_deviation_sum += deviation * deviation

        sample_variance = squared_deviation_sum / Decimal(n - 1)
        return_stdev = sample_variance.sqrt()

    if not return_sum.is_finite():
        raise ValueError(f"computed return_sum must be finite, got {return_sum}")
    if not mean_return.is_finite():
        raise ValueError(f"computed mean_return must be finite, got {mean_return}")
    if not squared_deviation_sum.is_finite():
        raise ValueError(
            f"computed squared_deviation_sum must be finite, got {squared_deviation_sum}"
        )
    if not sample_variance.is_finite():
        raise ValueError(f"computed sample_variance must be finite, got {sample_variance}")
    if sample_variance < Decimal(0):
        raise ValueError(f"computed sample_variance must be >= 0, got {sample_variance}")
    if not return_stdev.is_finite():
        raise ValueError(f"computed return_stdev must be finite, got {return_stdev}")
    if return_stdev <= Decimal(0):
        raise ValueError(
            f"return_stdev must be greater than zero to compute sharpe_ratio, got {return_stdev}"
        )

    with localcontext(_metrics_decimal_context()):
        sharpe_ratio = (mean_return - risk_free_per_period) / return_stdev

    if not sharpe_ratio.is_finite():
        raise ValueError(f"computed sharpe_ratio must be finite, got {sharpe_ratio}")

    return Stage2Metrics(
        mean_return=mean_return,
        return_stdev=return_stdev,
        sharpe_ratio=sharpe_ratio,
    )
