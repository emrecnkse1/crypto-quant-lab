"""Backtest cost model abstraction (BACKTEST_SPEC.md Bölüm 21, 22; COST_MODEL_SPEC.md).

`CostModel` is a minimal, structural (`typing.Protocol`) abstraction so the
backtest engine never becomes tightly coupled to a concrete cost
implementation. Faz 4 mandates exactly one concrete implementation —
`ZeroCostModel` — which always returns zero. Faz 5A adds realistic
proportional transaction-cost models (commission, spread, slippage) on top
of this unchanged Protocol (COST_MODEL_SPEC.md); funding remains deferred to
Faz 5B.

`quantity` is always the absolute (unsigned) traded quantity for a single
fill — never a signed position delta, and never tied to BUY/SELL side,
current position, target position, or policy. Example (BACKTEST_SPEC.md
Bölüm 16): both `FLAT -> LONG` and `LONG -> FLAT` trade `quantity=q`; a
`LONG -> SHORT` reversal trades `quantity=2q`. `execution_price` is the
fill's execution price — in Faz 4's next-open execution engine this is the
next candle's OPEN, but this module has no knowledge of that timing logic.
"""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ProportionalCommissionModel:
    """Faz 5A proportional taker-like commission (COST_MODEL_SPEC.md Bölüm 7, 8).

    `cost = quantity * execution_price * rate` — a symmetric, direction-
    independent fee on the fill's notional. No default `rate`: the caller
    must always supply one explicitly.
    """

    rate: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.rate, "rate")
        if not self.rate.is_finite():
            raise ValueError(f"rate must be finite, got {self.rate}")
        if self.rate < 0:
            raise ValueError(f"rate must be >= 0, got {self.rate}")

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        _require_decimal(quantity, "quantity")
        _require_decimal(execution_price, "execution_price")
        return quantity * execution_price * self.rate


@dataclass(frozen=True, slots=True)
class ProportionalSpreadCostModel:
    """Faz 5A proportional half-spread cost (COST_MODEL_SPEC.md Bölüm 9, 10).

    `cost = quantity * execution_price * half_spread_rate` — a single
    simulated fill pays an approximate half-spread against the OHLCV
    reference price, represented as a monetary cost rather than an
    execution-price adjustment. No default `half_spread_rate`: the caller
    must always supply one explicitly.
    """

    half_spread_rate: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.half_spread_rate, "half_spread_rate")
        if not self.half_spread_rate.is_finite():
            raise ValueError(f"half_spread_rate must be finite, got {self.half_spread_rate}")
        if self.half_spread_rate < 0:
            raise ValueError(f"half_spread_rate must be >= 0, got {self.half_spread_rate}")

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        _require_decimal(quantity, "quantity")
        _require_decimal(execution_price, "execution_price")
        return quantity * execution_price * self.half_spread_rate


@dataclass(frozen=True, slots=True)
class ProportionalSlippageCostModel:
    """Faz 5A fixed-proportional slippage approximation (COST_MODEL_SPEC.md Bölüm 11, 12).

    `cost = quantity * execution_price * rate` — a fixed-proportional
    friction on the fill's notional, represented as a monetary cost rather
    than an execution-price adjustment. Size/depth/volatility-dependent
    slippage and market impact are deferred. No default `rate`: the caller
    must always supply one explicitly.
    """

    rate: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.rate, "rate")
        if not self.rate.is_finite():
            raise ValueError(f"rate must be finite, got {self.rate}")
        if self.rate < 0:
            raise ValueError(f"rate must be >= 0, got {self.rate}")

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        _require_decimal(quantity, "quantity")
        _require_decimal(execution_price, "execution_price")
        return quantity * execution_price * self.rate


@dataclass(frozen=True, slots=True)
class CompositeCostModel:
    """Faz 5A deterministic composition of cost models (COST_MODEL_SPEC.md Bölüm 16-19).

    `components` is invoked in exact left-to-right tuple order, each exactly
    once, and their `calculate_cost` outputs are summed. Composite does not
    interpret component semantics: negative component outputs (and a
    negative total) are legal, non-finite Decimal outputs are not rejected
    (only non-Decimal outputs are), and an empty `components` tuple is a
    legal zero-cost edge case (though `ZeroCostModel` remains the preferred
    explicit zero-friction baseline). No default `components`.
    """

    components: tuple[CostModel, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple):
            raise TypeError(f"components must be a tuple, got {type(self.components).__name__}")

    def calculate_cost(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        _require_decimal(quantity, "quantity")
        _require_decimal(execution_price, "execution_price")
        total = Decimal(0)
        for index, component in enumerate(self.components):
            component_cost = component.calculate_cost(
                quantity=quantity, execution_price=execution_price
            )
            _require_decimal(component_cost, f"components[{index}] calculate_cost result")
            total += component_cost
        return total
