"""Pure next-open fill transition engine (BACKTEST_SPEC.md Bölüm 9, 10, 11, 13, 14, 15, 16, 21, 22).

Wires a single simulation transition: a policy's `PositionTarget`, the
current `AccountState`, the signal candle N and its chronological successor
candle N+1, and a `CostModel`, into a deterministic account transition
executed at N+1's OPEN. No full dataset loop — that belongs to a later
microstep (BACKTEST_SPEC.md Bölüm 35, madde 9). No `Fill`/`Trade`/`Order`
entities, no store/network/quality-gate access.
"""

from dataclasses import dataclass
from decimal import Decimal

from crypto_quant_lab.backtest.accounting import AccountState, apply_fill
from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.market_data.models import Candle


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The outcome of a single executed fill — not a `Fill`/`Trade` log entity."""

    state: AccountState
    quantity_delta: Decimal
    execution_price: Decimal
    cost: Decimal


def target_quantity_for(target: PositionTarget, position_quantity: Decimal) -> Decimal:
    """Map a policy's desired `PositionTarget` to a signed target quantity (BACKTEST_SPEC.md Bölüm 13)."""
    if not isinstance(target, PositionTarget):
        raise TypeError(f"target must be a PositionTarget, got {type(target).__name__}")
    if not isinstance(position_quantity, Decimal):
        raise TypeError(
            f"position_quantity must be a Decimal, got {type(position_quantity).__name__}"
        )
    if position_quantity <= 0:
        raise ValueError(f"position_quantity must be > 0, got {position_quantity}")

    if target is PositionTarget.LONG:
        return position_quantity
    if target is PositionTarget.SHORT:
        return -position_quantity
    return Decimal(0)


def execute_target_on_next_candle(
    state: AccountState,
    *,
    target: PositionTarget,
    config: BacktestConfig,
    signal_candle: Candle,
    next_candle: Candle,
    cost_model: CostModel,
) -> ExecutionResult | None:
    """Execute `target` at `next_candle`'s OPEN, returning the new state or `None` for a no-op.

    All validation (input types, current-position legality, candle
    metadata/timing consistency) runs before the no-op short-circuit, so a
    malformed candle pair can never silently slip through under cover of a
    same-target no-op. `next_candle` must be the exact chronological
    successor of `signal_candle` — this is checked by reusing
    `feature_availability_time`, not by re-deriving the boundary math.
    """
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    if not isinstance(target, PositionTarget):
        raise TypeError(f"target must be a PositionTarget, got {type(target).__name__}")
    if not isinstance(config, BacktestConfig):
        raise TypeError(f"config must be a BacktestConfig, got {type(config).__name__}")
    if not isinstance(signal_candle, Candle):
        raise TypeError(f"signal_candle must be a Candle, got {type(signal_candle).__name__}")
    if not isinstance(next_candle, Candle):
        raise TypeError(f"next_candle must be a Candle, got {type(next_candle).__name__}")

    position_quantity = config.position_quantity
    if state.position_quantity not in (-position_quantity, Decimal(0), position_quantity):
        raise ValueError(
            "state.position_quantity must be one of "
            f"{{-{position_quantity}, 0, {position_quantity}}} for a fixed-target Faz 4 engine, "
            f"got {state.position_quantity!r}"
        )

    if signal_candle.symbol != next_candle.symbol:
        raise ValueError(
            "signal_candle and next_candle must share the same symbol: "
            f"{signal_candle.symbol!r} != {next_candle.symbol!r}"
        )
    if signal_candle.timeframe != next_candle.timeframe:
        raise ValueError(
            "signal_candle and next_candle must share the same timeframe: "
            f"{signal_candle.timeframe!r} != {next_candle.timeframe!r}"
        )

    signal_availability = feature_availability_time(signal_candle)
    if signal_availability != next_candle.open_time:
        raise ValueError(
            "next_candle must be the exact chronological successor of signal_candle: "
            f"feature_availability_time(signal_candle)={signal_availability!r}, "
            f"next_candle.open_time={next_candle.open_time!r}"
        )

    target_quantity = target_quantity_for(target, position_quantity)
    quantity_delta = target_quantity - state.position_quantity

    if quantity_delta == Decimal(0):
        return None

    execution_price = next_candle.open
    cost = cost_model.calculate_cost(quantity=abs(quantity_delta), execution_price=execution_price)

    new_state = apply_fill(
        state, quantity_delta=quantity_delta, execution_price=execution_price, fill_cost=cost
    )

    return ExecutionResult(
        state=new_state,
        quantity_delta=quantity_delta,
        execution_price=execution_price,
        cost=cost,
    )
