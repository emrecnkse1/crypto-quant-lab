"""Deterministic in-memory replay loop (BACKTEST_SPEC.md Bölüm 3, 9, 10, 11, 28, 30, 31, 35 madde 9).

`run_backtest_replay` is the first component to wire the full chain: a
validated `tuple[Candle, ...]` -> candle-by-candle iteration -> available
prefix -> `PolicyContext` -> policy target -> next-open execution ->
`AccountState` -> cost/fill/trade accumulation -> equity curve ->
`BacktestResult`. Purely in-memory and offline — no `HistoricalCandleStore`,
no `prepare_backtest_dataset`, no network, no wall clock, no randomness.
Store-backed integration is a later microstep (Bölüm 35, madde 10).

Every math/validation primitive here is reused, never duplicated: dataset
timing/order/availability checks reuse `feature_availability_time` and
`datetime_to_epoch_us`; fills reuse `execute_target_on_next_candle`; result
assembly reuses `build_equity_point`/`trade_count_for_transition`/
`build_backtest_result`.
"""

from datetime import datetime
from decimal import Decimal

from crypto_quant_lab.backtest.accounting import AccountState
from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.execution import execute_target_on_next_candle
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.backtest.policy import BacktestPolicy, PolicyContext
from crypto_quant_lab.backtest.results import (
    build_backtest_result,
    build_equity_point,
    trade_count_for_transition,
)
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def _validate_dataset(candles: tuple[Candle, ...], *, as_of_time: datetime) -> None:
    """Validate the entire dataset before any policy/cost_model call (BACKTEST_SPEC.md Bölüm 30, 31).

    Checks, in order, for every candle: genuine type, shared symbol/
    timeframe, strictly ascending/unique open_time, exact contiguous
    cadence, and availability no later than the global `as_of_time`.
    Unsupported timeframes are rejected for free — `feature_availability_time`
    already raises for them, no separate whitelist is written here.
    """
    if not isinstance(candles, tuple):
        raise TypeError(f"candles must be a tuple, got {type(candles).__name__}")
    if not candles:
        raise ValueError("candles must not be empty")

    as_of_us = datetime_to_epoch_us(as_of_time)

    first_symbol: str | None = None
    first_timeframe: str | None = None
    previous_open_us: int | None = None
    previous_availability: datetime | None = None

    for candle in candles:
        if not isinstance(candle, Candle):
            raise TypeError(f"candles must contain Candle instances, got {type(candle).__name__}")

        if first_symbol is None:
            first_symbol = candle.symbol
            first_timeframe = candle.timeframe
        else:
            if candle.symbol != first_symbol:
                raise ValueError(
                    f"all candles must share the same symbol: {first_symbol!r} != {candle.symbol!r}"
                )
            if candle.timeframe != first_timeframe:
                raise ValueError(
                    "all candles must share the same timeframe: "
                    f"{first_timeframe!r} != {candle.timeframe!r}"
                )

        open_us = datetime_to_epoch_us(candle.open_time)
        if previous_open_us is not None and open_us <= previous_open_us:
            raise ValueError(
                "candles must be strictly ascending and unique by open_time: "
                f"open_time_us={open_us} does not follow previous_us={previous_open_us}"
            )

        availability = feature_availability_time(candle)
        if datetime_to_epoch_us(availability) > as_of_us:
            raise ValueError(
                "candle is not yet available as of the global as_of_time: "
                f"feature_availability_time(candle)={availability!r}, as_of_time={as_of_time!r}"
            )

        if previous_availability is not None and previous_availability != candle.open_time:
            raise ValueError(
                "candles must be exactly contiguous: previous candle's availability time "
                f"{previous_availability!r} != next candle's open_time {candle.open_time!r}"
            )

        previous_open_us = open_us
        previous_availability = availability


def run_backtest_replay(
    candles: tuple[Candle, ...],
    *,
    as_of_time: datetime,
    config: BacktestConfig,
    policy: BacktestPolicy,
    cost_model: CostModel,
) -> BacktestResult:
    """Run a deterministic, bar-by-bar backtest replay over `candles`.

    `as_of_time` is the global run boundary (BACKTEST_SPEC.md Bölüm 3) — it
    is never used as a `PolicyContext.as_of_time`; each step instead uses
    `feature_availability_time(candle_N)`, keeping the policy's own claimed
    "as of" instant consistent with the available prefix it is actually
    shown. For each candle, in order: mark equity with the state as carried
    in from the previous step, build the available-prefix `PolicyContext`,
    call the policy, and — unless this is the dataset's last candle
    (BACKTEST_SPEC.md Bölüm 11: no next candle, no fill, no fabricated
    price) — execute the resulting target at the next candle's OPEN via
    `execute_target_on_next_candle`. Equity is marked *before* attempting
    the current candle's own execution: a fill triggered by candle N-1's
    signal already lands at candle N's OPEN (before N's availability
    boundary), so it must be reflected; a fill triggered by candle N's own
    signal only lands at N+1's OPEN, so it must not be.
    """
    datetime_to_epoch_us(as_of_time)
    if not isinstance(config, BacktestConfig):
        raise TypeError(f"config must be a BacktestConfig, got {type(config).__name__}")
    _validate_dataset(candles, as_of_time=as_of_time)

    state = AccountState(
        cash=config.initial_cash,
        position_quantity=Decimal(0),
        average_entry_price=None,
        realized_pnl=Decimal(0),
    )
    total_cost = Decimal(0)
    fill_count = 0
    trade_count = 0
    equity_points: list[EquityPoint] = []

    last_index = len(candles) - 1
    for i, candle in enumerate(candles):
        equity_points.append(build_equity_point(state, candle=candle))

        context = PolicyContext(
            as_of_time=feature_availability_time(candle), candles=candles[: i + 1]
        )
        target = policy.target_position(context)
        if not isinstance(target, PositionTarget):
            raise TypeError(
                f"policy.target_position must return a PositionTarget, got {type(target).__name__}"
            )

        if i == last_index:
            continue

        old_state = state
        execution_result = execute_target_on_next_candle(
            state,
            target=target,
            config=config,
            signal_candle=candle,
            next_candle=candles[i + 1],
            cost_model=cost_model,
        )

        if execution_result is not None:
            state = execution_result.state
            total_cost += execution_result.cost
            fill_count += 1
            trade_count += trade_count_for_transition(
                old_state.position_quantity, state.position_quantity
            )

    return build_backtest_result(
        initial_cash=config.initial_cash,
        final_state=state,
        final_mark_price=candles[-1].close,
        total_cost=total_cost,
        fill_count=fill_count,
        trade_count=trade_count,
        equity_curve=tuple(equity_points),
    )
