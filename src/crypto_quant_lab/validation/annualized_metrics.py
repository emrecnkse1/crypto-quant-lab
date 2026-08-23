"""Annualized Metrics — Sharpe, Sortino, CAGR, Calmar (VALIDATION_SPEC.md Bölüm 15.19-15.33, 28.H).

Four independent, additive functions, each a bare-`Decimal` reuse of the
already-locked Stage-1 (`compute_stage1_metrics`) and Stage-2
(`compute_periodic_returns`, `compute_stage2_metrics`) foundations plus the
existing Faz 3 `candle_duration` timeframe primitive. No second return-series,
mean/stdev, or drawdown algorithm is introduced here; no new dataclass/value
object is introduced (Bölüm 15.21); `Stage1Metrics`, `Stage2Metrics`,
`BacktestResult`, `WindowResult`, `Candidate`, and `Trial` are all unchanged.

Calendar basis is a fixed, non-configurable 365-day year (Bölüm 15.22) —
never 365.25 or 252 trading days. `timeframe` is a required, keyword-only
`str` on every function; annualization information is never inferred from
equity-curve timestamps or result/config metadata (Bölüm 15.14, 15.22).

This module never imports `rolling`, `windows`, or `candidate` — it has no
cross-window aggregation, no candidate/trial coupling, and no orchestration
dependency (Bölüm 15.29).
"""

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from crypto_quant_lab.backtest.models import BacktestResult
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns,
    compute_stage1_metrics,
    compute_stage2_metrics,
)

_YEAR_MICROSECONDS = 365 * 86_400 * 1_000_000


def _annualized_metrics_decimal_context() -> Context:
    """A fresh, explicit computation context — never the caller's ambient one.

    Redefined here (not cross-module imported) because `metrics.py`'s
    `_metrics_decimal_context` is module-private (`_`-prefixed) and the
    repository convention forbids cross-module import of private helpers
    (Bölüm 15.28). Same exact shape as Stage-1/Stage-2's context.
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


def _require_timeframe(timeframe: object) -> None:
    if not isinstance(timeframe, str):
        raise TypeError(f"timeframe must be a str, got {type(timeframe).__name__}")


def _timedelta_to_microseconds(duration: timedelta) -> int:
    # timedelta.total_seconds() is never used here (it returns a float) —
    # the same exact-integer technique as
    # storage/sqlite_codec.datetime_to_epoch_us is reused on a bare
    # timedelta (Bölüm 15.22).
    return duration.days * 86_400_000_000 + duration.seconds * 1_000_000 + duration.microseconds


def _periods_per_year(timeframe: str) -> Decimal:
    duration_us = _timedelta_to_microseconds(candle_duration(timeframe))
    return Decimal(_YEAR_MICROSECONDS) / Decimal(duration_us)


def compute_annualized_sharpe_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
    risk_free_per_period: Decimal = Decimal(0),
) -> Decimal:
    """Annualize Stage-2's non-annualized `sharpe_ratio` (VALIDATION_SPEC.md Bölüm 15.23, 15.27).

    `compute_stage2_metrics` is reused in full — its entire validation chain
    and its `sharpe_ratio` arithmetic are never reimplemented here, and any
    exception it raises (type and message unchanged) propagates as-is. Only
    once that call succeeds is the annualization factor
    (`_periods_per_year(timeframe).sqrt()`) computed and multiplied onto
    `sharpe_ratio` — in exactly that order, never an algebraically
    equivalent rewrite that annualizes mean/stdev separately.
    """
    _require_timeframe(timeframe)
    candle_duration(timeframe)

    stage2_metrics = compute_stage2_metrics(result, risk_free_per_period=risk_free_per_period)

    with localcontext(_annualized_metrics_decimal_context()):
        annualization_factor = _periods_per_year(timeframe).sqrt()
        annualized_sharpe = stage2_metrics.sharpe_ratio * annualization_factor

    if not annualized_sharpe.is_finite():
        raise ValueError(
            f"computed annualized Sharpe ratio must be finite, got {annualized_sharpe}"
        )

    return annualized_sharpe


def compute_sortino_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
    minimum_acceptable_return_per_period: Decimal = Decimal(0),
) -> Decimal:
    """Annualized Sortino ratio (VALIDATION_SPEC.md Bölüm 15.24, 15.27).

    `compute_periodic_returns` is consumed (never `compute_stage2_metrics`):
    Sortino's validity is independent of Stage-2's total-return-stdev being
    positive — a flat total-stdev result can still legally have a positive
    downside deviation. Downside deviation uses a population divisor (`n`,
    not Stage-2's sample `n - 1`) over ALL `n` observations, each
    non-downside observation contributing zero to the squared-deviation sum.
    """
    _require_timeframe(timeframe)
    candle_duration(timeframe)

    if not isinstance(minimum_acceptable_return_per_period, Decimal):
        raise TypeError(
            "minimum_acceptable_return_per_period must be a Decimal, got "
            f"{type(minimum_acceptable_return_per_period).__name__}"
        )
    if not minimum_acceptable_return_per_period.is_finite():
        raise ValueError(
            "minimum_acceptable_return_per_period must be finite, got "
            f"{minimum_acceptable_return_per_period}"
        )

    returns = compute_periodic_returns(result)

    return_count = len(returns)
    if return_count < 2:
        raise ValueError(
            "at least two periodic returns are required to compute the Sortino ratio, "
            f"got {return_count}"
        )

    with localcontext(_annualized_metrics_decimal_context()):
        n = len(returns)
        return_sum = sum(returns, Decimal(0))
        mean_return = return_sum / Decimal(n)

        downside_squared_sum = Decimal(0)
        for periodic_return in returns:
            deviation = periodic_return - minimum_acceptable_return_per_period
            downside_deviation_term = min(Decimal(0), deviation)
            downside_squared_sum += downside_deviation_term * downside_deviation_term

        downside_variance = downside_squared_sum / Decimal(n)
        downside_deviation = downside_variance.sqrt()

    if not downside_deviation.is_finite() or downside_deviation <= Decimal(0):
        raise ValueError(
            "downside_deviation must be finite and greater than zero to compute the Sortino "
            f"ratio, got {downside_deviation}"
        )

    with localcontext(_annualized_metrics_decimal_context()):
        per_period_sortino = (
            mean_return - minimum_acceptable_return_per_period
        ) / downside_deviation
        annualization_factor = _periods_per_year(timeframe).sqrt()
        sortino_ratio = per_period_sortino * annualization_factor

    if not sortino_ratio.is_finite():
        raise ValueError(f"computed Sortino ratio must be finite, got {sortino_ratio}")

    return sortino_ratio


def compute_cagr(
    result: BacktestResult,
    *,
    timeframe: str,
) -> Decimal:
    """Compound Annual Growth Rate (VALIDATION_SPEC.md Bölüm 15.25, 15.27).

    Uses return-period count (`n = len(result.equity_curve)`) plus
    `periods_per_year` rather than elapsed wall-clock time — no result model
    in this repository carries a mechanically valid "initial_cash occurred
    at" timestamp, and grid-aligned candle cadence makes the period-count
    form the semantic equivalent of elapsed-time annualization.
    `total_return` is reused from `compute_stage1_metrics` (never an
    independently recomputed `final_equity / initial_cash`).
    """
    _require_timeframe(timeframe)
    candle_duration(timeframe)

    stage1_metrics = compute_stage1_metrics(result)
    n = len(result.equity_curve)

    with localcontext(_annualized_metrics_decimal_context()):
        periods_per_year = _periods_per_year(timeframe)
        base = Decimal(1) + stage1_metrics.total_return
        exponent = periods_per_year / Decimal(n)
        cagr = base**exponent - Decimal(1)

    if not cagr.is_finite():
        raise ValueError(f"computed CAGR must be finite, got {cagr}")

    return cagr


def compute_calmar_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
) -> Decimal:
    """Calmar ratio (VALIDATION_SPEC.md Bölüm 15.26, 15.27).

    `compute_cagr` and `compute_stage1_metrics(result).max_drawdown` are both
    reused unchanged — no second CAGR or drawdown algorithm is introduced.
    Any failure from `compute_cagr` (including an undefined CAGR) propagates
    with its type and message unchanged.
    """
    cagr = compute_cagr(result, timeframe=timeframe)
    max_drawdown = compute_stage1_metrics(result).max_drawdown

    with localcontext(_annualized_metrics_decimal_context()):
        calmar_ratio = cagr / max_drawdown

    if not calmar_ratio.is_finite():
        raise ValueError(f"computed Calmar ratio must be finite, got {calmar_ratio}")

    return calmar_ratio
