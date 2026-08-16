"""Deterministic in-memory replay loop (BACKTEST_SPEC.md Bölüm 3, 9, 10, 11, 28, 30, 31, 35 madde 9;
FUNDING_SPEC.md Bölüm 8-14, FUNDING-SPEC MS9 locked timing contract).

`run_backtest_replay` is the first component to wire the full chain: a
validated `tuple[Candle, ...]` -> candle-by-candle iteration -> available
prefix -> `PolicyContext` -> policy target -> next-open execution ->
`AccountState` -> cost/fill/trade accumulation -> equity curve ->
`BacktestResult`. Purely in-memory and offline — no `HistoricalCandleStore`,
no `prepare_backtest_dataset`, no network, no wall clock, no randomness.
Store-backed integration is a later microstep (Bölüm 35, madde 10).

`funding_events`/`funding_model` are additive, keyword-only, defaulted
parameters (FUNDING-SPEC MS10): with `funding_events=()` this function is
byte/value-identical to its pre-funding behavior. When supplied, every
iteration first sweeps in all not-yet-consumed funding events with
`event_time <= this candle's availability instant`, applying each
individually (never pre-summed) against the *pre-fill* `AccountState.
position_quantity` — the locked FUNDING-SPEC MS9 same-timestamp priority
is funding settlement(s), then the equity mark, then policy evaluation,
then this iteration's own next-open fill. No new `EquityPoint` is ever
created for a funding event; its cash effect is visible starting at the
mark it was swept in before (immediate, if concurrent with that mark) or
the next scheduled mark (if it fell strictly between two marks). Funding
carries no store/quality/network knowledge here — `funding_events` is
trusted only as far as its own explicit structural/order/range contract;
whether that sequence is *coverage-complete* is a higher-layer (future
FUNDING-SPEC MS11 store-runner) responsibility, never decided in this loop.

Every math/validation primitive here is reused, never duplicated: dataset
timing/order/availability checks reuse `feature_availability_time` and
`datetime_to_epoch_us`; fills reuse `execute_target_on_next_candle`; the
funding cash effect reuses `apply_cash_cost`; result assembly reuses
`build_equity_point`/`trade_count_for_transition`/`build_backtest_result`.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from crypto_quant_lab.backtest.accounting import AccountState, apply_cash_cost
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
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.models import HistoricalFundingEvent
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


def _validate_funding_events(
    funding_events: Sequence[HistoricalFundingEvent],
    *,
    symbol: str,
    run_start_us: int,
    run_end_us: int,
) -> None:
    """Validate explicit funding input structure/partition/range/order (FUNDING_SPEC.md Bölüm 10, 11).

    Mirrors `_validate_dataset`'s philosophy: replay trusts nothing about
    its funding input and fails loudly rather than filtering/sorting/
    repairing. A plain `Candle` carries no `exchange`/`market_type`, so the
    only external ground truth available is `symbol`; `exchange`/
    `market_type` are instead required to be internally consistent across
    the whole `funding_events` sequence (derived from its first element).
    """
    if not funding_events:
        return

    first = funding_events[0]
    if not isinstance(first, HistoricalFundingEvent):
        raise TypeError(
            "funding_events must contain HistoricalFundingEvent instances, got "
            f"{type(first).__name__}"
        )
    if first.symbol != symbol:
        raise ValueError(
            f"funding_events symbol must match the candle dataset's symbol: expected {symbol!r}, "
            f"got {first.symbol!r}"
        )
    expected_exchange = first.exchange
    expected_market_type = first.market_type

    seen_keys: set[tuple[str, str, str, datetime, str]] = set()
    previous_sort_key: tuple[int, str] | None = None

    for event in funding_events:
        if not isinstance(event, HistoricalFundingEvent):
            raise TypeError(
                "funding_events must contain HistoricalFundingEvent instances, got "
                f"{type(event).__name__}"
            )
        if (
            event.exchange != expected_exchange
            or event.market_type != expected_market_type
            or event.symbol != symbol
        ):
            raise ValueError(
                "funding_events must share one consistent exchange/market_type/symbol "
                f"partition: expected ({expected_exchange!r}, {expected_market_type!r}, "
                f"{symbol!r}), got ({event.exchange!r}, {event.market_type!r}, {event.symbol!r})"
            )

        event_time_us = datetime_to_epoch_us(event.funding.event_time)
        if not (run_start_us <= event_time_us < run_end_us):
            raise ValueError(
                "funding event_time must satisfy run_start <= event_time < run_end: "
                f"event_time={event.funding.event_time!r}"
            )

        sort_key = (event_time_us, event.funding.rate_type)
        if previous_sort_key is not None and sort_key < previous_sort_key:
            raise ValueError(
                "funding_events are not ordered (event_time, rate_type) ascending: "
                f"{sort_key!r} follows {previous_sort_key!r}"
            )
        previous_sort_key = sort_key

        key = event.canonical_key
        if key in seen_keys:
            raise ValueError(f"duplicate canonical funding event key: {key!r}")
        seen_keys.add(key)


def _apply_due_funding_events(
    state: AccountState,
    *,
    funding_events: Sequence[HistoricalFundingEvent],
    cursor: int,
    mark_time_us: int,
    funding_model: FundingModel | None,
) -> tuple[AccountState, Decimal, int]:
    """Apply every not-yet-consumed funding event with `event_time <= mark_time_us`.

    Each event is processed individually against `state` as carried in —
    never pre-summed (FUNDING_DATA_SPEC.md Bölüm 7) — using the pre-fill
    position (FUNDING_SPEC.md Bölüm 12, locked by FUNDING-SPEC MS9). A
    total no-op when `funding_events` is empty: `funding_model` is never
    dereferenced in that case, so `None` is always safe then.
    """
    funding_cost_sum = Decimal(0)
    while cursor < len(funding_events):
        event = funding_events[cursor]
        event_time_us = datetime_to_epoch_us(event.funding.event_time)
        if event_time_us > mark_time_us:
            break

        funding_cost = funding_model.calculate_funding_cost(
            signed_position_quantity=state.position_quantity,
            reference_price=event.funding.reference_price,
            funding_rate=event.funding.funding_rate,
        )
        state = apply_cash_cost(state, cost=funding_cost)
        funding_cost_sum += funding_cost
        cursor += 1

    return state, funding_cost_sum, cursor


def run_backtest_replay(
    candles: tuple[Candle, ...],
    *,
    as_of_time: datetime,
    config: BacktestConfig,
    policy: BacktestPolicy,
    cost_model: CostModel,
    funding_events: Sequence[HistoricalFundingEvent] = (),
    funding_model: FundingModel | None = None,
) -> BacktestResult:
    """Run a deterministic, bar-by-bar backtest replay over `candles`.

    `as_of_time` is the global run boundary (BACKTEST_SPEC.md Bölüm 3) — it
    is never used as a `PolicyContext.as_of_time`; each step instead uses
    `feature_availability_time(candle_N)`, keeping the policy's own claimed
    "as of" instant consistent with the available prefix it is actually
    shown. For each candle, in order: sweep in and apply any due funding
    events against the pre-fill position (FUNDING-SPEC MS9 locked
    priority), mark equity with the resulting state, build the
    available-prefix `PolicyContext`, call the policy, and — unless this is
    the dataset's last candle (BACKTEST_SPEC.md Bölüm 11: no next candle,
    no fill, no fabricated price) — execute the resulting target at the
    next candle's OPEN via `execute_target_on_next_candle`. Equity is
    marked *before* attempting the current candle's own execution: a fill
    triggered by candle N-1's signal already lands at candle N's OPEN
    (before N's availability boundary), so it must be reflected; a fill
    triggered by candle N's own signal only lands at N+1's OPEN, so it must
    not be — funding due exactly at that shared boundary is swept in
    before the mark either way, so its cash effect is always visible in
    the mark labeled with that same instant.

    `funding_events`' canonical range is derived from `candles` itself:
    `[candles[0].open_time, feature_availability_time(candles[-1]))` — an
    event exactly at the final candle's availability instant is therefore
    never legal input (excluded by the same half-open convention the
    funding store itself uses), and one before the first candle's open is
    never legal either. Supplying a non-empty `funding_events` without a
    `funding_model` is rejected before any candle is processed — funding
    history is never silently treated as zero cost.
    """
    datetime_to_epoch_us(as_of_time)
    if not isinstance(config, BacktestConfig):
        raise TypeError(f"config must be a BacktestConfig, got {type(config).__name__}")
    _validate_dataset(candles, as_of_time=as_of_time)

    if funding_events and funding_model is None:
        raise ValueError("funding_model must be supplied when funding_events is non-empty")

    run_start_us = datetime_to_epoch_us(candles[0].open_time)
    run_end_us = datetime_to_epoch_us(feature_availability_time(candles[-1]))
    _validate_funding_events(
        funding_events, symbol=candles[0].symbol, run_start_us=run_start_us, run_end_us=run_end_us
    )

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
    funding_cursor = 0

    last_index = len(candles) - 1
    for i, candle in enumerate(candles):
        availability_time = feature_availability_time(candle)

        state, funding_cost_sum, funding_cursor = _apply_due_funding_events(
            state,
            funding_events=funding_events,
            cursor=funding_cursor,
            mark_time_us=datetime_to_epoch_us(availability_time),
            funding_model=funding_model,
        )
        total_cost += funding_cost_sum

        equity_points.append(build_equity_point(state, candle=candle))

        context = PolicyContext(as_of_time=availability_time, candles=candles[: i + 1])
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
