"""OHLCV candle data model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol cannot be empty")
        if not self.timeframe:
            raise ValueError("timeframe cannot be empty")
        if self.open_time.tzinfo is None or self.open_time.utcoffset() is None:
            raise ValueError("open_time must be timezone-aware")
        for field_name in ("open", "high", "low", "close", "volume"):
            if not getattr(self, field_name).is_finite():
                raise ValueError(f"{field_name} must be finite")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < self.open or self.high < self.low or self.high < self.close:
            raise ValueError("high cannot be less than open, low or close")
        if self.low > self.open or self.low > self.high or self.low > self.close:
            raise ValueError("low cannot be greater than open, high or close")
