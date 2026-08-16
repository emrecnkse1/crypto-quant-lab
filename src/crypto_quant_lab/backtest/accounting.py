"""Pure position/accounting primitives (BACKTEST_SPEC.md Bölüm 14, 16, 17, 18, 19, 20, 22).

`AccountState` is the immutable core numeric ledger (cash, signed net
position, average entry price, cumulative realized PnL). `apply_fill` is a
pure state transition — no simulation loop, no Fill/Trade/Order entities, no
`CostModel` invocation (cost arrives precomputed), no store/quality-gate
access. Only the exact transitions Bölüm 16's table describes are legal:
opening from flat, fully closing to flat, and full LONG<->SHORT reversal.
Same-direction pyramiding and partial close are rejected — Faz 4's
fixed-target policy model (Bölüm 13) structurally never produces them.
"""

from dataclasses import dataclass
from decimal import Decimal


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class AccountState:
    """The core numeric ledger (BACKTEST_SPEC.md Bölüm 17).

    `average_entry_price` is `None` exactly when `position_quantity == 0`
    (BACKTEST_SPEC.md Bölüm 17: "entry_price... flat iken None") — any other
    combination is rejected.
    """

    cash: Decimal
    position_quantity: Decimal
    average_entry_price: Decimal | None
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.cash, "cash")
        _require_decimal(self.position_quantity, "position_quantity")
        _require_decimal(self.realized_pnl, "realized_pnl")

        if self.average_entry_price is not None:
            _require_decimal(self.average_entry_price, "average_entry_price")

        is_flat = self.position_quantity == Decimal(0)
        has_entry_price = self.average_entry_price is not None
        if is_flat and has_entry_price:
            raise ValueError(
                "average_entry_price must be None when position_quantity == 0, got "
                f"{self.average_entry_price!r}"
            )
        if not is_flat and not has_entry_price:
            raise ValueError(
                "average_entry_price must be a Decimal when position_quantity != 0, got None"
            )


def apply_fill(
    state: AccountState,
    *,
    quantity_delta: Decimal,
    execution_price: Decimal,
    fill_cost: Decimal,
) -> AccountState:
    """Apply a single signed fill to `state`, returning a new `AccountState`.

    `quantity_delta` is signed (positive=buy, negative=sell) — unlike
    `CostModel.calculate_cost`'s unsigned `quantity`, accounting needs
    direction to compute cash movement. Only opening from flat, fully
    closing to flat, and full reversal (sign flip through zero) are legal
    (BACKTEST_SPEC.md Bölüm 16); same-direction pyramiding and partial close
    raise `ValueError`, since Faz 4's fixed-target policy model never
    produces them.
    """
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    _require_decimal(quantity_delta, "quantity_delta")
    _require_decimal(execution_price, "execution_price")
    _require_decimal(fill_cost, "fill_cost")

    if quantity_delta == Decimal(0):
        raise ValueError("quantity_delta must not be zero: a no-op transition produces no fill")

    cash_after = state.cash - quantity_delta * execution_price - fill_cost

    old_quantity = state.position_quantity
    new_quantity = old_quantity + quantity_delta
    realized_pnl_after = state.realized_pnl

    if old_quantity == Decimal(0):
        new_entry_price = execution_price
    elif new_quantity == Decimal(0):
        closed_quantity = abs(old_quantity)
        if old_quantity > 0:
            realized_pnl_after += closed_quantity * (execution_price - state.average_entry_price)
        else:
            realized_pnl_after += closed_quantity * (state.average_entry_price - execution_price)
        new_entry_price = None
    elif (old_quantity > 0) != (new_quantity > 0):
        closed_quantity = abs(old_quantity)
        if old_quantity > 0:
            realized_pnl_after += closed_quantity * (execution_price - state.average_entry_price)
        else:
            realized_pnl_after += closed_quantity * (state.average_entry_price - execution_price)
        new_entry_price = execution_price
    else:
        raise ValueError(
            "apply_fill only supports opening from flat, fully closing, or reversing a "
            "position — same-direction increase or partial close is not a legal Faz 4 "
            f"transition: old_quantity={old_quantity!r}, quantity_delta={quantity_delta!r}, "
            f"new_quantity={new_quantity!r}"
        )

    return AccountState(
        cash=cash_after,
        position_quantity=new_quantity,
        average_entry_price=new_entry_price,
        realized_pnl=realized_pnl_after,
    )


def apply_cash_cost(
    state: AccountState,
    *,
    cost: Decimal,
) -> AccountState:
    """Apply a signed cash-only cost to `state`, returning a new `AccountState`.

    Generic — no funding-specific knowledge. `cost > 0` reduces cash (an
    outflow); `cost < 0` increases it (an inflow/rebate); `position_quantity`,
    `average_entry_price`, and `realized_pnl` are preserved exactly. Unlike
    `apply_fill`, this never represents an execution: no position/entry/
    realized-PnL change, and no fill/trade-count effect (those are owned by
    the replay loop, not `AccountState`).
    """
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    _require_decimal(cost, "cost")

    return AccountState(
        cash=state.cash - cost,
        position_quantity=state.position_quantity,
        average_entry_price=state.average_entry_price,
        realized_pnl=state.realized_pnl,
    )


def unrealized_pnl(state: AccountState, *, mark_price: Decimal) -> Decimal:
    """Mark-to-market unrealized PnL (BACKTEST_SPEC.md Bölüm 19) — zero when flat."""
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    _require_decimal(mark_price, "mark_price")

    if state.position_quantity == Decimal(0):
        return Decimal(0)

    return (mark_price - state.average_entry_price) * state.position_quantity


def equity(state: AccountState, *, mark_price: Decimal) -> Decimal:
    """Total account equity (BACKTEST_SPEC.md Bölüm 17): `cash + position_quantity * mark_price`."""
    if not isinstance(state, AccountState):
        raise TypeError(f"state must be an AccountState, got {type(state).__name__}")
    _require_decimal(mark_price, "mark_price")

    return state.cash + state.position_quantity * mark_price
