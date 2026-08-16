"""Pure funding cost calculator (FUNDING_SPEC.md Bölüm 4.2, 4.3, 4.4, 6, 8.2).

`FundingModel` is a minimal, structural (`typing.Protocol`) abstraction,
mirroring `backtest.costs.CostModel`'s existing style, so a future replay
integration is not tightly coupled to one concrete funding formula.
`LinearFundingModel` implements the locked linear USDⓈ-margined perpetual
formula — the only formula FUNDING_SPEC.md currently locks.

Exchange-neutral and data-store-neutral: no Binance/source-adapter
knowledge, no `FundingEvent`/`HistoricalFundingStore` dependency, no
`CostModel` dependency (FUNDING_SPEC.md Bölüm 3: never composed into
`CompositeCostModel`), and no event-time argument — funding is a discrete
settlement against the position held at `event_time`, not a duration-
weighted calculation, and event chronology/ordering belongs entirely to a
future replay microstep (Bölüm 8.3, 12).
"""

from decimal import Decimal
from typing import Protocol


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


class FundingModel(Protocol):
    def calculate_funding_cost(
        self,
        *,
        signed_position_quantity: Decimal,
        reference_price: Decimal,
        funding_rate: Decimal,
    ) -> Decimal: ...


class LinearFundingModel:
    """Locked linear USDⓈ-margined perpetual funding formula (FUNDING_SPEC.md Bölüm 4.3).

    `funding_cost = signed_position_quantity * reference_price * funding_rate`
    — no leading minus, no `abs()`, no side branching. Positive result means
    cash outflow (the account pays); negative means cash inflow (the account
    receives). A flat position or a zero rate naturally yields
    `Decimal("0")` from the formula itself — no special case is written for
    either (Bölüm 8.2).
    """

    def calculate_funding_cost(
        self,
        *,
        signed_position_quantity: Decimal,
        reference_price: Decimal,
        funding_rate: Decimal,
    ) -> Decimal:
        _require_decimal(signed_position_quantity, "signed_position_quantity")

        _require_decimal(reference_price, "reference_price")
        if not reference_price.is_finite():
            raise ValueError(f"reference_price must be finite, got {reference_price}")
        if reference_price <= 0:
            raise ValueError(f"reference_price must be > 0, got {reference_price}")

        _require_decimal(funding_rate, "funding_rate")
        if not funding_rate.is_finite():
            raise ValueError(f"funding_rate must be finite, got {funding_rate}")

        return signed_position_quantity * reference_price * funding_rate
