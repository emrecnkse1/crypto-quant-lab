"""First in-memory multi-leg replay: spot long + USDⓈ-M perpetual short (FUNDING_RESEARCH_SPEC.md Bölüm 19.12).

Pure and offline: no store, file, network, wall clock or randomness. Wires
two trade-candle series, exogenous scripted intents and settled funding
events into the Bölüm 19.10.1 accounting (`backtest/multileg.py`) opened
with `EventOrdering.FUNDING_BEFORE_FILL` (E1). No second PnL/funding engine:
every cash effect goes through the accounting API, every cost through the
caller's `CostModel`s, every funding amount through the caller's
`FundingModel`.

Timing (single-leg contract reused, BACKTEST_SPEC.md Bölüm 8-11, 18;
FUNDING_SPEC.md Bölüm 11-13): for candle N at t = availability(N) =
open_time(N) + timeframe, in this order —
  1. every not-yet-consumed funding event with event_time <= t is settled
     against the PRE-FILL position (open short: accounting API; flat or
     closed: a zero record, the API is not called — decision R4);
  2. the equity mark with both candles' CLOSE (a pre-fill view);
  3. the intent whose decision_time == t (if any) is evaluated;
  4. its paired fill at candle N+1's OPEN, stamped open_time(N+1) == t.
     The last candle has no N+1: its intent is reported as unexecuted.
Decisions only use information available at t; candle N+1's OPEN is the
execution price source, never part of the decision. The intents are
exogenous/scripted: timestamp validation proves the replay's access rules,
not that the script was written without hindsight.

First-replay decisions (R1–R5, Bölüm 19.12.6): identical open_time grids
for both series, no forward-fill; MS9 order via E1; a pair still open at the
end stays open and is valued at the last trade CLOSEs (no synthetic close,
no closing cost); flat funding produces a zero record; no warmup (the whole
run is evaluated). "Mark" means trade-CLOSE valuation here, never the
exchange markPrice series (not ingested). Arithmetic runs in the caller's
Decimal context; research callers apply decimal_policy.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.multileg import (
    EventOrdering,
    HedgedLifecycleSummary,
    HedgedPair,
    HedgedPortfolioMark,
    HedgedPortfolioState,
    HedgeLifecycle,
    LegExecution,
    LegSide,
    PairedFill,
    TradableInstrument,
    apply_hedged_close,
    apply_hedged_open,
    apply_perpetual_funding,
    mark_hedged_portfolio,
    new_hedged_portfolio,
    summarize_closed_hedge,
)
from crypto_quant_lab.backtest.replay import _validate_dataset, _validate_funding_events
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.models import HistoricalFundingEvent
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.datasets import CandleDataset
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


class HedgeAction(Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class TraceKind(Enum):
    FUNDING_SETTLED = "FUNDING_SETTLED"
    FUNDING_ZERO_RECORDED = "FUNDING_ZERO_RECORDED"
    MARK_PRE_FILL = "MARK_PRE_FILL"
    PAIRED_FILL = "PAIRED_FILL"
    INTENT_UNEXECUTED = "INTENT_UNEXECUTED"


@dataclass(frozen=True, slots=True)
class HedgeIntent:
    """An exogenous, scripted open/close decision (no price: fills use the next OPENs)."""

    action: HedgeAction
    decision_time: datetime
    quantity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.action, HedgeAction):
            raise TypeError("action must be a HedgeAction")
        datetime_to_epoch_us(self.decision_time)
        if not isinstance(self.quantity, Decimal):
            raise TypeError(f"quantity must be a Decimal, got {type(self.quantity).__name__}")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError(f"quantity must be a finite Decimal > 0, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class LegCandles:
    """One tradable leg's price series with its dataset identity."""

    dataset: CandleDataset
    candles: tuple[Candle, ...]


@dataclass(frozen=True, slots=True)
class FundingRecord:
    event: HistoricalFundingEvent
    lifecycle_before: HedgeLifecycle
    pre_fill_perpetual_quantity: Decimal
    signed_cost: Decimal  # FundingModel sign: positive paid, negative received
    applied_to_open_position: bool


@dataclass(frozen=True, slots=True)
class ReplayEquityPoint:
    """Equity at `time` = availability of the candle whose CLOSEs value it; PRE-fill."""

    candle_open_time: datetime
    mark: HedgedPortfolioMark
    pre_fill: bool = True


@dataclass(frozen=True, slots=True)
class TraceEvent:
    time: datetime
    kind: TraceKind
    detail: str


@dataclass(frozen=True, slots=True)
class UnexecutedIntent:
    intent: HedgeIntent
    reason: str


@dataclass(frozen=True, slots=True)
class MultiLegReplayResult:
    pair: HedgedPair
    spot_dataset: CandleDataset
    perpetual_dataset: CandleDataset
    run_start: datetime
    run_end: datetime
    as_of_time: datetime
    initial_state: HedgedPortfolioState
    final_state: HedgedPortfolioState
    paired_fills: tuple[PairedFill, ...]
    funding_records: tuple[FundingRecord, ...]
    equity_points: tuple[ReplayEquityPoint, ...]
    trace: tuple[TraceEvent, ...]
    unexecuted_intents: tuple[UnexecutedIntent, ...]
    final_mark: HedgedPortfolioMark
    closed_summary: HedgedLifecycleSummary | None
    intents_are_exogenous: bool = True
    valuation_source: str = "trade_close"
    liquidation_not_modeled: bool = True
    legging_not_modeled: bool = True
    warmup_supported: bool = False

    @property
    def position_open_at_end(self) -> bool:
        return self.final_state.lifecycle is HedgeLifecycle.HEDGED_OPEN


# ---------------------------------------------------------------- validation


def _validate_leg(leg: LegCandles, instrument: TradableInstrument, name: str, as_of: datetime):
    if not isinstance(leg, LegCandles):
        raise TypeError(f"{name} must be LegCandles, got {type(leg).__name__}")
    derived = TradableInstrument.from_candle_dataset(
        leg.dataset, quote_asset=instrument.quote_asset
    )
    if derived != instrument:
        raise ValueError(f"{name} dataset {leg.dataset!r} is not the pair's {name} instrument")
    _validate_dataset(leg.candles, as_of_time=as_of)  # single-leg rules: order, gaps, as_of
    first = leg.candles[0]
    if (first.symbol, first.timeframe) != (leg.dataset.symbol, leg.dataset.timeframe):
        raise ValueError(
            f"{name} candles ({first.symbol!r}, {first.timeframe!r}) do not match the dataset "
            f"({leg.dataset.symbol!r}, {leg.dataset.timeframe!r})"
        )


def _validate_funding_identity(events: tuple, pair: HedgedPair) -> None:
    if not isinstance(events, tuple):
        raise TypeError(f"funding_events must be a tuple, got {type(events).__name__}")
    seen: dict = {}
    perp = pair.perpetual
    for event in events:
        if not isinstance(event, HistoricalFundingEvent):
            raise TypeError(f"funding_events must contain HistoricalFundingEvent, got {event!r}")
        if (event.exchange, event.market_type, event.symbol) != (
            perp.exchange,
            perp.market_type,
            perp.symbol,
        ):
            raise ValueError(
                f"funding event {event.canonical_key!r} is not for the pair's perpetual"
            )
        previous = seen.get(event.canonical_key)
        if previous is not None:
            if previous.funding == event.funding:
                raise ValueError(f"duplicate funding event {event.canonical_key!r}")
            raise ValueError(f"conflicting payload for funding event {event.canonical_key!r}")
        seen[event.canonical_key] = event


def _validate_intents(intents: tuple, availability: dict[int, int]) -> dict[int, HedgeIntent]:
    """Intent script: at most OPEN then CLOSE, same quantity, strictly later, on candle closes."""
    if not isinstance(intents, tuple):
        raise TypeError(f"intents must be a tuple, got {type(intents).__name__}")
    by_time: dict[int, HedgeIntent] = {}
    previous_us = None
    for index, intent in enumerate(intents):
        if not isinstance(intent, HedgeIntent):
            raise TypeError(f"intents[{index}] must be a HedgeIntent")
        time_us = datetime_to_epoch_us(intent.decision_time)
        if time_us not in availability:
            raise ValueError(
                f"intents[{index}] decision_time {intent.decision_time!r} is not a candle "
                "availability instant (open_time + timeframe) of the run"
            )
        if previous_us is not None and time_us <= previous_us:
            raise ValueError(
                f"intents[{index}] must be strictly later than the previous intent "
                "(one decision per instant)"
            )
        previous_us = time_us
        by_time[time_us] = intent
    actions = [intent.action for intent in intents]
    if actions not in ([], [HedgeAction.OPEN], [HedgeAction.OPEN, HedgeAction.CLOSE]):
        raise ValueError(
            f"intent script must be [], [OPEN] or [OPEN, CLOSE] (one lifecycle), got "
            f"{[a.value for a in actions]}"
        )
    if len(intents) == 2 and intents[0].quantity != intents[1].quantity:
        raise ValueError("CLOSE quantity must equal the OPEN quantity (no partial close)")
    return by_time


# ---------------------------------------------------------------- replay


def run_multileg_replay(
    *,
    pair: HedgedPair,
    spot: LegCandles,
    perpetual: LegCandles,
    funding_events: tuple[HistoricalFundingEvent, ...],
    intents: tuple[HedgeIntent, ...],
    spot_cash: Decimal,
    perpetual_collateral: Decimal,
    spot_cost_model: CostModel,
    perpetual_cost_model: CostModel,
    funding_model: FundingModel,
    as_of_time: datetime,
) -> MultiLegReplayResult:
    """Replay one hedged pair over two synchronized candle series (see module docstring)."""
    if not isinstance(pair, HedgedPair):
        raise TypeError(f"pair must be a HedgedPair, got {type(pair).__name__}")
    datetime_to_epoch_us(as_of_time)
    _validate_leg(spot, pair.spot, "spot", as_of_time)
    _validate_leg(perpetual, pair.perpetual, "perpetual", as_of_time)
    spot_candles, perp_candles = spot.candles, perpetual.candles
    if spot.dataset.timeframe != perpetual.dataset.timeframe:
        raise ValueError("spot and perpetual series must share one timeframe")
    spot_opens = [datetime_to_epoch_us(c.open_time) for c in spot_candles]
    perp_opens = [datetime_to_epoch_us(c.open_time) for c in perp_candles]
    if spot_opens != perp_opens:
        raise ValueError(
            "spot and perpetual series must have identical open_time grids (no forward-fill, "
            "no partial overlap)"
        )
    run_start = spot_candles[0].open_time
    run_end = feature_availability_time(spot_candles[-1])
    _validate_funding_identity(funding_events, pair)
    _validate_funding_events(
        funding_events,
        symbol=pair.perpetual.symbol,
        run_start_us=datetime_to_epoch_us(run_start),
        run_end_us=datetime_to_epoch_us(run_end),
    )
    availability = {
        datetime_to_epoch_us(feature_availability_time(c)): i for i, c in enumerate(spot_candles)
    }
    intents_by_time = _validate_intents(intents, availability)

    initial = new_hedged_portfolio(
        pair,
        spot_cash=spot_cash,
        perpetual_collateral=perpetual_collateral,
        event_ordering=EventOrdering.FUNDING_BEFORE_FILL,
    )
    state = initial
    funding_records: list[FundingRecord] = []
    equity_points: list[ReplayEquityPoint] = []
    trace: list[TraceEvent] = []
    unexecuted: list[UnexecutedIntent] = []
    cursor = 0
    last_index = len(spot_candles) - 1
    for index, (spot_candle, perp_candle) in enumerate(
        zip(spot_candles, perp_candles, strict=True)
    ):
        t = feature_availability_time(spot_candle)
        t_us = datetime_to_epoch_us(t)
        # 1. funding settlements due by t, against the pre-fill position
        while cursor < len(funding_events):
            event = funding_events[cursor]
            if datetime_to_epoch_us(event.funding.event_time) > t_us:
                break
            cursor += 1
            lifecycle = state.lifecycle
            quantity = state.perpetual.quantity
            if lifecycle is HedgeLifecycle.HEDGED_OPEN:
                before = state.perpetual.funding_paid
                state = apply_perpetual_funding(state, event, funding_model=funding_model)
                cost = state.perpetual.funding_paid - before
                kind = TraceKind.FUNDING_SETTLED
            else:
                cost = funding_model.calculate_funding_cost(
                    signed_position_quantity=quantity,
                    reference_price=event.funding.reference_price,
                    funding_rate=event.funding.funding_rate,
                )
                if not isinstance(cost, Decimal) or cost != 0:
                    raise ValueError(f"funding on a flat perpetual leg must be 0, got {cost!r}")
                kind = TraceKind.FUNDING_ZERO_RECORDED
            funding_records.append(
                FundingRecord(event, lifecycle, quantity, cost, kind is TraceKind.FUNDING_SETTLED)
            )
            trace.append(
                TraceEvent(
                    event.funding.event_time,
                    kind,
                    f"{event.funding.rate_type} rate {event.funding.funding_rate} on perpetual "
                    f"quantity {quantity}: signed cost {cost}",
                )
            )
        # 2. pre-fill equity mark at this candle's CLOSEs
        mark = mark_hedged_portfolio(
            state, time=t, spot_mark_price=spot_candle.close, perpetual_mark_price=perp_candle.close
        )
        equity_points.append(ReplayEquityPoint(spot_candle.open_time, mark))
        trace.append(
            TraceEvent(t, TraceKind.MARK_PRE_FILL, f"portfolio equity {mark.portfolio_equity}")
        )
        # 3-4. intent evaluation and paired fill at the next candles' OPEN
        intent = intents_by_time.get(t_us)
        if intent is None:
            continue
        if index == last_index:
            reason = "no next candle: the last candle's decision is never filled"
            unexecuted.append(UnexecutedIntent(intent, reason))
            trace.append(
                TraceEvent(t, TraceKind.INTENT_UNEXECUTED, f"{intent.action.value}: {reason}")
            )
            continue
        next_spot, next_perp = spot_candles[index + 1], perp_candles[index + 1]
        fill_time = next_spot.open_time
        opening = intent.action is HedgeAction.OPEN
        spot_leg = LegExecution(
            pair.spot,
            LegSide.BUY if opening else LegSide.SELL,
            intent.quantity,
            next_spot.open,
            fill_time,
        )
        perp_leg = LegExecution(
            pair.perpetual,
            LegSide.SELL if opening else LegSide.BUY,
            intent.quantity,
            next_perp.open,
            fill_time,
        )
        apply = apply_hedged_open if opening else apply_hedged_close
        state = apply(
            state,
            spot=spot_leg,
            perpetual=perp_leg,
            spot_cost_model=spot_cost_model,
            perpetual_cost_model=perpetual_cost_model,
        )
        fill = state.fills[-1]
        trace.append(
            TraceEvent(
                fill_time,
                TraceKind.PAIRED_FILL,
                f"{intent.action.value} qty {intent.quantity}: spot {next_spot.open} "
                f"(cost {fill.spot.cost}), perpetual {next_perp.open} (cost {fill.perpetual.cost})",
            )
        )
    if cursor != len(funding_events):  # every event is < run_end, so all must be consumed
        raise ValueError(f"{len(funding_events) - cursor} funding event(s) were not settled")
    last_spot, last_perp = spot_candles[-1], perp_candles[-1]
    final_mark = mark_hedged_portfolio(
        state, time=run_end, spot_mark_price=last_spot.close, perpetual_mark_price=last_perp.close
    )
    closed = state.lifecycle is HedgeLifecycle.FLAT_CLOSED
    return MultiLegReplayResult(
        pair=pair,
        spot_dataset=spot.dataset,
        perpetual_dataset=perpetual.dataset,
        run_start=run_start,
        run_end=run_end,
        as_of_time=as_of_time,
        initial_state=initial,
        final_state=state,
        paired_fills=state.fills,
        funding_records=tuple(funding_records),
        equity_points=tuple(equity_points),
        trace=tuple(trace),
        unexecuted_intents=tuple(unexecuted),
        final_mark=final_mark,
        closed_summary=summarize_closed_hedge(state) if closed else None,
    )
