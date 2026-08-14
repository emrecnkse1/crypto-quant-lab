"""Pure policy/context abstraction (BACKTEST_SPEC.md Bölüm 8, 9, 12).

`PolicyContext` is the immutable, anti-lookahead-validated available prefix
a policy sees at a given simulation instant. `BacktestPolicy` is a minimal,
structural (`typing.Protocol`) abstraction so the future simulation engine
never becomes tightly coupled to a concrete policy implementation. Neither
computes quantity, orders, fills, trades, or cost — only the desired
`PositionTarget` (BACKTEST_SPEC.md Bölüm 13: policy selects LONG/FLAT/SHORT,
never a quantity).

Pure — no simulation loop, no store/quality-gate access, no cost/accounting.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crypto_quant_lab.backtest.models import PositionTarget
from crypto_quant_lab.data_quality.feature_availability import is_candle_available_for_features
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """The immutable available-candle prefix a policy sees at `as_of_time`.

    Every candle must already be available (BACKTEST_SPEC.md Bölüm 8/9) —
    `is_candle_available_for_features(candle, as_of_time)` must hold for
    each one, not merely the last. Candles must be strictly ascending and
    unique by `open_time` — no silent sort/dedupe/repair.
    """

    as_of_time: datetime
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        datetime_to_epoch_us(self.as_of_time)

        if not isinstance(self.candles, tuple):
            raise TypeError(f"candles must be a tuple, got {type(self.candles).__name__}")

        previous_us: int | None = None
        for candle in self.candles:
            if not isinstance(candle, Candle):
                raise TypeError(
                    f"candles must contain Candle instances, got {type(candle).__name__}"
                )

            if not is_candle_available_for_features(candle, self.as_of_time):
                raise ValueError(
                    "PolicyContext cannot contain a candle unavailable at as_of_time: "
                    f"open_time={candle.open_time!r}, as_of_time={self.as_of_time!r}"
                )

            open_time_us = datetime_to_epoch_us(candle.open_time)
            if previous_us is not None and open_time_us <= previous_us:
                raise ValueError(
                    "PolicyContext candles must be strictly ascending with no duplicates: "
                    f"open_time_us={open_time_us} does not follow previous_us={previous_us}"
                )
            previous_us = open_time_us


class BacktestPolicy(Protocol):
    def target_position(self, context: PolicyContext) -> PositionTarget: ...
