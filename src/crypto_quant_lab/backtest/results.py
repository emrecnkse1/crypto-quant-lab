"""Pure equity/PnL/result assembly primitives (BACKTEST_SPEC.md Bölüm 17, 18, 19, 27, 28).

Aggregates an already-computed `AccountState` (and, for the equity curve, one
`AccountState` per available candle) into the locked `EquityPoint`/
`BacktestResult` shapes. No candle loop, no policy invocation, no execution
scheduling, no store/network/wall-clock access — those belong to Microstep 9
(BACKTEST_SPEC.md Bölüm 35, madde 9). No `Fill`/`Trade`/`Order` log entities.
"""

from decimal import Decimal

from crypto_quant_lab.backtest.accounting import AccountState, equity, unrealized_pnl
from crypto_quant_lab.backtest.models import BacktestResult, EquityPoint
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


def build_equity_point(state: AccountState, *, candle: Candle) -> EquityPoint:
    """Mark `state` at `candle`'s CLOSE, timestamped at its availability boundary.

    `EquityPoint.time` is `feature_availability_time(candle)`, not
    `candle.open_time` — `candle.close` is only usable once that boundary is
    reached (BACKTEST_SPEC.md Bölüm 18), so marking at `open_time` would
    itself be a lookahead violation.
    """
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    if not isinstance(candle, Candle):
        raise TypeError(f"candle must be a Candle, got {type(candle).__name__}")

    return EquityPoint(
        time=feature_availability_time(candle),
        equity=equity(state, mark_price=candle.close),
    )


def trade_count_for_transition(old_quantity: Decimal, new_quantity: Decimal) -> int:
    """Trade-transition-log entry count for a single fill (BACKTEST_SPEC.md Bölüm 19).

    Not a physical fill count and not a completed-round-trip count: a
    reversal is one fill but two transition-log entries (close + open).
    Same-direction changes to a different non-zero magnitude are not a legal
    Faz 4 transition (BACKTEST_SPEC.md Bölüm 16) and raise `ValueError`.
    """
    _require_decimal(old_quantity, "old_quantity")
    _require_decimal(new_quantity, "new_quantity")

    if old_quantity == new_quantity:
        return 0
    if old_quantity == Decimal(0) or new_quantity == Decimal(0):
        return 1
    if (old_quantity > 0) != (new_quantity > 0):
        return 2

    raise ValueError(
        "trade_count_for_transition only supports opening from flat, fully closing, or "
        f"reversing a position: old_quantity={old_quantity!r}, new_quantity={new_quantity!r}"
    )


def build_backtest_result(
    *,
    initial_cash: Decimal,
    final_state: AccountState,
    final_mark_price: Decimal,
    total_cost: Decimal,
    fill_count: int,
    trade_count: int,
    equity_curve: tuple[EquityPoint, ...],
) -> BacktestResult:
    """Assemble the locked `BacktestResult` (BACKTEST_SPEC.md Bölüm 27) from scalar aggregates.

    Takes already-summed aggregates (`total_cost`, `fill_count`,
    `trade_count`) rather than a collection of executions, keeping result
    assembly decoupled from the execution layer — Microstep 9's loop owns
    the accumulation. `total_pnl` is computed directly as
    `final_equity - initial_cash`, then cross-checked against the
    independent `realized + unrealized - cost` decomposition
    (BACKTEST_SPEC.md Bölüm 17) as a consistency invariant. Never creates a
    synthetic closing fill: an open position at the end is marked, not closed.
    """
    if not isinstance(final_state, AccountState):
        raise TypeError(f"final_state must be an AccountState, got {type(final_state).__name__}")

    final_cash = final_state.cash
    total_realized_pnl = final_state.realized_pnl
    total_unrealized_pnl = unrealized_pnl(final_state, mark_price=final_mark_price)
    final_equity = equity(final_state, mark_price=final_mark_price)

    total_pnl = final_equity - initial_cash
    expected_total_pnl = total_realized_pnl + total_unrealized_pnl - total_cost
    if total_pnl != expected_total_pnl:
        raise ValueError(
            "total_pnl consistency invariant violated: "
            f"final_equity - initial_cash={total_pnl!r} != "
            f"total_realized_pnl + total_unrealized_pnl - total_cost={expected_total_pnl!r}"
        )

    if not isinstance(equity_curve, tuple):
        raise TypeError(f"equity_curve must be a tuple, got {type(equity_curve).__name__}")

    previous_us = None
    for point in equity_curve:
        if not isinstance(point, EquityPoint):
            raise TypeError(
                f"equity_curve elements must be EquityPoint, got {type(point).__name__}"
            )
        current_us = datetime_to_epoch_us(point.time)
        if previous_us is not None and current_us <= previous_us:
            raise ValueError(
                "equity_curve times must be strictly ascending: "
                f"{current_us!r} does not follow {previous_us!r}"
            )
        previous_us = current_us

    if equity_curve and equity_curve[-1].equity != final_equity:
        raise ValueError(
            "equity_curve's final point must match final_equity: "
            f"equity_curve[-1].equity={equity_curve[-1].equity!r} != final_equity={final_equity!r}"
        )

    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=final_cash,
        final_equity=final_equity,
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        total_pnl=total_pnl,
        total_cost=total_cost,
        fill_count=fill_count,
        trade_count=trade_count,
        equity_curve=equity_curve,
    )
