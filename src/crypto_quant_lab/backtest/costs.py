"""Backtest cost model abstraction (BACKTEST_SPEC.md Bölüm 21, 22).

`CostModel` is a minimal, structural (`typing.Protocol`) abstraction so the
backtest engine never becomes tightly coupled to a concrete cost
implementation. Faz 4 mandates exactly one concrete implementation —
`ZeroCostModel` — which always returns zero; realistic costs (commission,
spread, slippage, funding) are deferred to Faz 5 (BACKTEST_SPEC.md Bölüm 34).

`quantity` is always the absolute (unsigned) traded quantity for a single
fill — never a signed position delta, and never tied to BUY/SELL side,
current position, target position, or policy. Example (BACKTEST_SPEC.md
Bölüm 16): both `FLAT -> LONG` and `LONG -> FLAT` trade `quantity=q`; a
`LONG -> SHORT` reversal trades `quantity=2q`. `execution_price` is the
fill's execution price — in Faz 4's future next-open execution engine this
will be the next candle's OPEN, but this module has no knowledge of that
timing logic.
"""

from decimal import Decimal
from typing import Protocol


class CostModel(Protocol):
    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal: ...


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


class ZeroCostModel:
    """Faz 4's mandatory `CostModel`: every fill costs exactly zero."""

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        _require_decimal(quantity, "quantity")
        _require_decimal(execution_price, "execution_price")
        return Decimal(0)
