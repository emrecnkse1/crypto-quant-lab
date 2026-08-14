"""Passive core domain primitives for the Faz 4 backtest engine (BACKTEST_SPEC.md).

Pure, immutable data containers only — no simulation loop, no accounting
engine, no fill execution, no policy execution, no store access, no quality
gate, no cost calculation. Those are later microsteps' responsibility
(BACKTEST_SPEC.md Bölüm 35).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_RESULT_FINANCIAL_FIELDS = (
    "initial_cash",
    "final_cash",
    "final_equity",
    "total_realized_pnl",
    "total_unrealized_pnl",
    "total_pnl",
    "total_cost",
)


class PositionTarget(Enum):
    """The policy's desired position state (BACKTEST_SPEC.md Bölüm 12).

    Not an order side, not a current position — the target state a policy
    wants the engine to reach.
    """

    LONG = "LONG"
    FLAT = "FLAT"
    SHORT = "SHORT"


def _require_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")


def _require_non_negative_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value}")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Minimum locked backtest configuration (BACKTEST_SPEC.md Bölüm 13, 17)."""

    initial_cash: Decimal
    position_quantity: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.initial_cash, "initial_cash")
        _require_decimal(self.position_quantity, "position_quantity")
        if self.position_quantity <= 0:
            raise ValueError(f"position_quantity must be > 0, got {self.position_quantity}")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A single candle-by-candle equity curve sample (BACKTEST_SPEC.md Bölüm 17: `list[(datetime, Decimal)]`)."""

    time: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        datetime_to_epoch_us(self.time)
        _require_decimal(self.equity, "equity")


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Minimum backtest result contract (BACKTEST_SPEC.md Bölüm 27)."""

    initial_cash: Decimal
    final_cash: Decimal
    final_equity: Decimal
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_pnl: Decimal
    total_cost: Decimal
    fill_count: int
    trade_count: int
    equity_curve: tuple[EquityPoint, ...]

    def __post_init__(self) -> None:
        for field_name in _RESULT_FINANCIAL_FIELDS:
            _require_decimal(getattr(self, field_name), field_name)

        _require_non_negative_int(self.fill_count, "fill_count")
        _require_non_negative_int(self.trade_count, "trade_count")

        if not isinstance(self.equity_curve, tuple):
            raise TypeError(f"equity_curve must be a tuple, got {type(self.equity_curve).__name__}")
