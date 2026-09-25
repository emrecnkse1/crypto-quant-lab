"""First multi-leg accounting slice: spot long + USDⓈ-M perpetual short (FUNDING_RESEARCH_SPEC.md Bölüm 19.10).

Pure, immutable accounting for ONE hedged pair with two separate ledgers —
no replay/event loop, no strategy, no store access. Additive: the
single-leg engine (`AccountState`, `apply_fill`, `BacktestResult`, replay,
runners) is untouched and never imports this module.

Ledgers (one quote currency, no transfers between them):
- spot:      cash -= qty * price + cost on buy; cash += qty * price - cost on
             sell; spot value = cash + qty * spot_mark.
- perpetual: collateral changes ONLY by costs, realized PnL and funding —
             the notional is never cash. Short unrealized PnL =
             (mark - entry) * signed_qty (= |qty| * (entry - mark)); margin
             equity = collateral + unrealized PnL.
- portfolio equity = spot cash + spot qty * spot mark + perpetual collateral
             + perpetual unrealized PnL. Nothing else is added (no notional,
             no index/mark value, no already-booked realized PnL, funding or
             cost).

First-slice limits (temporary, K1–K7 in Bölüm 19.11): no liquidation model
(margin equity may be shown negative; `liquidation_not_modeled` is always
True), fixed 1:1 base-quantity hedge, atomic paired fills only (same
timestamp and quantity; no legging/partial fills), spot long + perpetual
short only, no lot/tick quantization, two fixed wallets, lifecycle
FLAT -> HEDGED_OPEN -> FLAT_CLOSED. Funding uses the existing
`FundingModel` (signed: positive = paid) and applies only while the pair is
open, once per canonical event key, strictly between open and close times;
with the default `EventOrdering.STRICT_TIME` a funding event at the open or
close instant is rejected as ambiguous. `EventOrdering.FUNDING_BEFORE_FILL`
(E1, opt-in at construction, Bölüm 19.12.4) instead orders events by
(time, phase FUNDING < FILL, funding rate_type) — the locked single-leg MS9
tie order (FUNDING_SPEC.md Bölüm 12) — derived from the state's own recorded
fills and funding keys, never from a caller-supplied cursor. Arithmetic runs in the
caller's Decimal context, like the single-leg engine (COST_MODEL_SPEC.md
Bölüm 20); research callers pass an explicit context.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.models import HistoricalFundingEvent
from crypto_quant_lab.storage.datasets import (
    CONTRACT_TRADE,
    SPOT,
    SPOT_TRADE,
    USDM_PERPETUAL,
    CandleDataset,
)
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

_TRADABLE_PRICE_KIND = {SPOT: SPOT_TRADE, USDM_PERPETUAL: CONTRACT_TRADE}


class LegSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class HedgeLifecycle(Enum):
    FLAT = "FLAT"
    HEDGED_OPEN = "HEDGED_OPEN"
    FLAT_CLOSED = "FLAT_CLOSED"


class EventOrdering(Enum):
    """How state-changing events at one instant are ordered (Bölüm 19.12.4).

    STRICT_TIME (default, the first slice's behavior): every open, funding and
    close must be strictly later than the previous one.
    FUNDING_BEFORE_FILL (E1, opt-in): events are ordered by the key
    (time, phase, rate_type) with phase FUNDING (0) < FILL (1) — several
    fundings at one instant in ascending rate_type, then at most one fill.
    """

    STRICT_TIME = "STRICT_TIME"
    FUNDING_BEFORE_FILL = "FUNDING_BEFORE_FILL"


_PHASE_FUNDING = 0
_PHASE_FILL = 1


def _require_str(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    if positive and value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class TradableInstrument:
    """A tradable leg: Binance spot or USDⓈ-M perpetual, priced in `quote_asset`.

    Index-price and mark-price series are data, never tradable legs; use
    `from_candle_dataset` to derive an instrument from a price dataset — it
    rejects every non-tradable `price_kind`.
    """

    exchange: str
    market_type: str
    symbol: str
    quote_asset: str

    def __post_init__(self) -> None:
        for name in ("exchange", "market_type", "symbol", "quote_asset"):
            _require_str(getattr(self, name), name)
        if self.market_type not in _TRADABLE_PRICE_KIND:
            raise ValueError(
                f"market_type must be one of {tuple(_TRADABLE_PRICE_KIND)}, got "
                f"{self.market_type!r}"
            )

    @classmethod
    def from_candle_dataset(
        cls, dataset: CandleDataset, *, quote_asset: str
    ) -> "TradableInstrument":
        if not isinstance(dataset, CandleDataset):
            raise TypeError(f"dataset must be a CandleDataset, got {type(dataset).__name__}")
        expected = _TRADABLE_PRICE_KIND.get(dataset.market_type)
        if dataset.price_kind != expected:
            raise ValueError(
                f"price_kind {dataset.price_kind!r} of {dataset.market_type!r} is not a tradable "
                f"leg (expected {expected!r}); index/mark prices are data only"
            )
        return cls(dataset.exchange, dataset.market_type, dataset.symbol, quote_asset)


@dataclass(frozen=True, slots=True)
class HedgedPair:
    """Explicit spot/perpetual pairing (never inferred by parsing symbols); 1:1 only."""

    pair_id: str
    spot: TradableInstrument
    perpetual: TradableInstrument
    hedge_ratio: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        _require_str(self.pair_id, "pair_id")
        for name, instrument, market in (
            ("spot", self.spot, SPOT),
            ("perpetual", self.perpetual, USDM_PERPETUAL),
        ):
            if not isinstance(instrument, TradableInstrument):
                raise TypeError(f"{name} must be a TradableInstrument")
            if instrument.market_type != market:
                raise ValueError(f"{name} leg must have market_type {market!r}")
        if self.spot.exchange != self.perpetual.exchange:
            raise ValueError("both legs must be on the same exchange in this slice")
        if self.spot.quote_asset != self.perpetual.quote_asset:
            raise ValueError("both legs must share one quote asset (no FX in this slice)")
        _require_decimal(self.hedge_ratio, "hedge_ratio")
        if self.hedge_ratio != Decimal(1):
            raise ValueError("only a 1:1 base-quantity hedge is supported in this slice")


@dataclass(frozen=True, slots=True)
class LegExecution:
    """One leg's execution before costs: instrument, side, positive quantity, price, time."""

    instrument: TradableInstrument
    side: LegSide
    quantity: Decimal
    price: Decimal
    time: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, TradableInstrument):
            raise TypeError("instrument must be a TradableInstrument")
        if not isinstance(self.side, LegSide):
            raise TypeError("side must be a LegSide")
        _require_decimal(self.quantity, "quantity", positive=True)
        _require_decimal(self.price, "price", positive=True)
        datetime_to_epoch_us(self.time)


@dataclass(frozen=True, slots=True)
class LegFill:
    execution: LegExecution
    cost: Decimal


@dataclass(frozen=True, slots=True)
class PairedFill:
    """Both legs of one atomic open or close, same time and base quantity."""

    action: str  # "open" | "close"
    time: datetime
    spot: LegFill
    perpetual: LegFill


@dataclass(frozen=True, slots=True)
class SpotLedger:
    cash: Decimal
    quantity: Decimal
    entry_price: Decimal | None
    realized_pnl: Decimal
    costs_paid: Decimal


@dataclass(frozen=True, slots=True)
class PerpetualLedger:
    """`collateral` = initial + realized_pnl - costs_paid - funding_paid (checked)."""

    collateral: Decimal
    quantity: Decimal  # signed; <= 0 in this slice
    entry_price: Decimal | None
    realized_pnl: Decimal
    costs_paid: Decimal
    funding_paid: Decimal  # FundingModel sign: positive paid, negative received


@dataclass(frozen=True, slots=True)
class HedgedPortfolioState:
    pair: HedgedPair
    lifecycle: HedgeLifecycle
    initial_spot_cash: Decimal
    initial_perpetual_collateral: Decimal
    spot: SpotLedger
    perpetual: PerpetualLedger
    fills: tuple[PairedFill, ...]
    applied_funding_keys: tuple[tuple, ...]
    last_event_time: datetime | None
    event_ordering: EventOrdering = EventOrdering.STRICT_TIME

    def __post_init__(self) -> None:
        if not isinstance(self.event_ordering, EventOrdering):
            raise TypeError(
                f"event_ordering must be an EventOrdering, got {type(self.event_ordering).__name__}"
            )

    @property
    def initial_total_capital(self) -> Decimal:
        return self.initial_spot_cash + self.initial_perpetual_collateral

    @property
    def liquidation_not_modeled(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class HedgedPortfolioMark:
    """Mark-to-market view; `liquidation_not_modeled` — a negative margin equity is shown,
    never interpreted as survivable or liquidated."""

    time: datetime
    lifecycle: HedgeLifecycle
    spot_mark_price: Decimal
    perpetual_mark_price: Decimal
    spot_cash: Decimal
    spot_quantity: Decimal
    spot_asset_value: Decimal
    spot_equity: Decimal
    perpetual_collateral: Decimal
    perpetual_quantity: Decimal
    perpetual_unrealized_pnl: Decimal
    perpetual_margin_equity: Decimal
    portfolio_equity: Decimal
    initial_total_capital: Decimal
    total_pnl: Decimal
    liquidation_not_modeled: bool = True


@dataclass(frozen=True, slots=True)
class HedgedLifecycleSummary:
    pair_id: str
    fills: tuple[PairedFill, ...]
    initial_total_capital: Decimal
    final_spot_cash: Decimal
    final_perpetual_collateral: Decimal
    final_equity: Decimal
    spot_realized_pnl: Decimal
    perpetual_realized_pnl: Decimal
    spot_costs: Decimal
    perpetual_costs: Decimal
    funding_paid: Decimal
    total_pnl: Decimal
    liquidation_not_modeled: bool = True
    legging_not_modeled: bool = True


# ---------------------------------------------------------------- construction


def new_hedged_portfolio(
    pair: HedgedPair,
    *,
    spot_cash: Decimal,
    perpetual_collateral: Decimal,
    event_ordering: EventOrdering = EventOrdering.STRICT_TIME,
) -> HedgedPortfolioState:
    """A FLAT state with two explicitly funded wallets (both >= 0).

    `event_ordering` is fixed for the state's whole life; the default keeps the
    first slice's strict-time behavior.
    """
    if not isinstance(pair, HedgedPair):
        raise TypeError(f"pair must be a HedgedPair, got {type(pair).__name__}")
    for name, value in (("spot_cash", spot_cash), ("perpetual_collateral", perpetual_collateral)):
        _require_decimal(value, name)
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
    zero = Decimal(0)
    return HedgedPortfolioState(
        pair=pair,
        lifecycle=HedgeLifecycle.FLAT,
        initial_spot_cash=spot_cash,
        initial_perpetual_collateral=perpetual_collateral,
        spot=SpotLedger(spot_cash, zero, None, zero, zero),
        perpetual=PerpetualLedger(perpetual_collateral, zero, None, zero, zero, zero),
        fills=(),
        applied_funding_keys=(),
        last_event_time=None,
        event_ordering=event_ordering,
    )


# ---------------------------------------------------------------- validation helpers


def _require_state(state: object) -> HedgedPortfolioState:
    if not isinstance(state, HedgedPortfolioState):
        raise TypeError(f"state must be a HedgedPortfolioState, got {type(state).__name__}")
    return state


def _require_after_last_event(state: HedgedPortfolioState, time: datetime, what: str) -> None:
    time_us = datetime_to_epoch_us(time)
    if state.last_event_time is not None and time_us <= datetime_to_epoch_us(state.last_event_time):
        raise ValueError(
            f"{what} time {time!r} must be strictly after the last event {state.last_event_time!r}"
        )


def _recorded_event_cursor(state: HedgedPortfolioState) -> tuple[int, int, str] | None:
    """The latest (time_us, phase, rate_type) among the state's OWN recorded events.

    Derived from `fills` and `applied_funding_keys` (canonical_key[3] is the
    event time, [4] the rate_type), so a caller cannot move it independently.
    """
    keys = [(datetime_to_epoch_us(fill.time), _PHASE_FILL, "") for fill in state.fills]
    keys += [
        (datetime_to_epoch_us(key[3]), _PHASE_FUNDING, key[4]) for key in state.applied_funding_keys
    ]
    return max(keys) if keys else None


def _require_in_order(
    state: HedgedPortfolioState, time: datetime, phase: int, rate_type: str, what: str
) -> None:
    if state.event_ordering is EventOrdering.STRICT_TIME:
        _require_after_last_event(state, time, what)
        return
    cursor = _recorded_event_cursor(state)
    last_us = None if state.last_event_time is None else datetime_to_epoch_us(state.last_event_time)
    if (cursor is None) != (last_us is None) or (cursor is not None and cursor[0] != last_us):
        raise ValueError(
            "event history is inconsistent with last_event_time; same-instant ordering "
            "cannot be inferred and is refused"
        )
    key = (datetime_to_epoch_us(time), phase, rate_type)
    if cursor is not None and key <= cursor:
        raise ValueError(
            f"{what} at {time!r} violates FUNDING_BEFORE_FILL ordering: it must follow the last "
            "event by (time, phase FUNDING < FILL, rate_type)"
        )


def _leg_cost(cost_model: CostModel, execution: LegExecution, leg: str) -> Decimal:
    cost = cost_model.calculate_cost(quantity=execution.quantity, execution_price=execution.price)
    if not isinstance(cost, Decimal):
        raise TypeError(f"{leg} cost model returned {type(cost).__name__}, not a Decimal")
    if not cost.is_finite() or cost < 0:
        raise ValueError(f"{leg} cost model returned an invalid cost {cost}")
    return cost


def _validated_pair(
    state: HedgedPortfolioState,
    spot: LegExecution | None,
    perpetual: LegExecution | None,
    *,
    spot_side: LegSide,
    perpetual_side: LegSide,
    action: str,
) -> tuple[LegExecution, LegExecution]:
    if spot is None or perpetual is None:
        raise ValueError(f"atomic {action} needs both legs; a single-leg fill is not supported")
    for name, leg, instrument, side in (
        ("spot", spot, state.pair.spot, spot_side),
        ("perpetual", perpetual, state.pair.perpetual, perpetual_side),
    ):
        if not isinstance(leg, LegExecution):
            raise TypeError(f"{name} must be a LegExecution, got {type(leg).__name__}")
        if leg.instrument != instrument:
            raise ValueError(f"{name} leg instrument {leg.instrument!r} is not the pair's {name}")
        if leg.side is not side:
            raise ValueError(
                f"{action} requires {name} {side.value}, got {leg.side.value} "
                "(only spot long + perpetual short is supported)"
            )
    if datetime_to_epoch_us(spot.time) != datetime_to_epoch_us(perpetual.time):
        raise ValueError(f"paired {action} legs must share one timestamp")
    if spot.quantity != perpetual.quantity * state.pair.hedge_ratio:
        raise ValueError(
            f"paired {action} legs must have equal base quantity (1:1), got spot "
            f"{spot.quantity} and perpetual {perpetual.quantity}"
        )
    return spot, perpetual


# ---------------------------------------------------------------- transitions


def apply_hedged_open(
    state: HedgedPortfolioState,
    *,
    spot: LegExecution | None,
    perpetual: LegExecution | None,
    spot_cost_model: CostModel,
    perpetual_cost_model: CostModel,
) -> HedgedPortfolioState:
    """Atomically buy spot and short the perpetual; returns a NEW state or raises."""
    state = _require_state(state)
    if state.lifecycle is not HedgeLifecycle.FLAT:
        raise ValueError(f"open requires lifecycle FLAT, got {state.lifecycle.value}")
    spot, perpetual = _validated_pair(
        state, spot, perpetual, spot_side=LegSide.BUY, perpetual_side=LegSide.SELL, action="open"
    )
    _require_in_order(state, spot.time, _PHASE_FILL, "", "open")
    spot_cost = _leg_cost(spot_cost_model, spot, "spot")
    perpetual_cost = _leg_cost(perpetual_cost_model, perpetual, "perpetual")
    spot_cash = state.spot.cash - spot.quantity * spot.price - spot_cost
    if spot_cash < 0:
        raise ValueError(f"insufficient spot cash: the buy would leave {spot_cash}")
    new_spot = replace(
        state.spot,
        cash=spot_cash,
        quantity=state.spot.quantity + spot.quantity,
        entry_price=spot.price,
        costs_paid=state.spot.costs_paid + spot_cost,
    )
    new_perpetual = replace(
        state.perpetual,
        collateral=state.perpetual.collateral - perpetual_cost,  # notional is never cash
        quantity=state.perpetual.quantity - perpetual.quantity,
        entry_price=perpetual.price,
        costs_paid=state.perpetual.costs_paid + perpetual_cost,
    )
    fill = PairedFill(
        "open", spot.time, LegFill(spot, spot_cost), LegFill(perpetual, perpetual_cost)
    )
    return _checked(
        replace(
            state,
            lifecycle=HedgeLifecycle.HEDGED_OPEN,
            spot=new_spot,
            perpetual=new_perpetual,
            fills=(*state.fills, fill),
            last_event_time=spot.time,
        )
    )


def apply_hedged_close(
    state: HedgedPortfolioState,
    *,
    spot: LegExecution | None,
    perpetual: LegExecution | None,
    spot_cost_model: CostModel,
    perpetual_cost_model: CostModel,
) -> HedgedPortfolioState:
    """Atomically sell the whole spot position and buy back the whole perpetual short."""
    state = _require_state(state)
    if state.lifecycle is not HedgeLifecycle.HEDGED_OPEN:
        raise ValueError(f"close requires lifecycle HEDGED_OPEN, got {state.lifecycle.value}")
    spot, perpetual = _validated_pair(
        state, spot, perpetual, spot_side=LegSide.SELL, perpetual_side=LegSide.BUY, action="close"
    )
    if spot.quantity != state.spot.quantity or perpetual.quantity != -state.perpetual.quantity:
        raise ValueError(
            "close must cover exactly the open quantity (no partial close), open spot "
            f"{state.spot.quantity}, perpetual {state.perpetual.quantity}"
        )
    _require_in_order(state, spot.time, _PHASE_FILL, "", "close")
    spot_cost = _leg_cost(spot_cost_model, spot, "spot")
    perpetual_cost = _leg_cost(perpetual_cost_model, perpetual, "perpetual")
    spot_realized = spot.quantity * (spot.price - state.spot.entry_price)
    perpetual_realized = perpetual.quantity * (state.perpetual.entry_price - perpetual.price)
    new_spot = replace(
        state.spot,
        cash=state.spot.cash + spot.quantity * spot.price - spot_cost,
        quantity=Decimal(0),
        entry_price=None,
        realized_pnl=state.spot.realized_pnl + spot_realized,
        costs_paid=state.spot.costs_paid + spot_cost,
    )
    new_perpetual = replace(
        state.perpetual,
        collateral=state.perpetual.collateral + perpetual_realized - perpetual_cost,
        quantity=Decimal(0),
        entry_price=None,
        realized_pnl=state.perpetual.realized_pnl + perpetual_realized,
        costs_paid=state.perpetual.costs_paid + perpetual_cost,
    )
    fill = PairedFill(
        "close", spot.time, LegFill(spot, spot_cost), LegFill(perpetual, perpetual_cost)
    )
    return _checked(
        replace(
            state,
            lifecycle=HedgeLifecycle.FLAT_CLOSED,
            spot=new_spot,
            perpetual=new_perpetual,
            fills=(*state.fills, fill),
            last_event_time=spot.time,
        )
    )


def apply_perpetual_funding(
    state: HedgedPortfolioState, event: HistoricalFundingEvent, *, funding_model: FundingModel
) -> HedgedPortfolioState:
    """Settle one funding event on the open perpetual short (perpetual ledger only, once)."""
    state = _require_state(state)
    if not isinstance(event, HistoricalFundingEvent):
        raise TypeError(f"event must be a HistoricalFundingEvent, got {type(event).__name__}")
    if state.lifecycle is not HedgeLifecycle.HEDGED_OPEN:
        raise ValueError(
            f"funding needs an open perpetual leg, lifecycle is {state.lifecycle.value}"
        )
    perp = state.pair.perpetual
    if (event.exchange, event.market_type, event.symbol) != (
        perp.exchange,
        perp.market_type,
        perp.symbol,
    ):
        raise ValueError(
            f"funding event ({event.exchange!r}, {event.market_type!r}, {event.symbol!r}) is not "
            "for the pair's perpetual leg"
        )
    if event.canonical_key in state.applied_funding_keys:
        raise ValueError(f"funding event {event.canonical_key!r} was already applied")
    _require_in_order(
        state, event.funding.event_time, _PHASE_FUNDING, event.funding.rate_type, "funding"
    )
    cost = funding_model.calculate_funding_cost(
        signed_position_quantity=state.perpetual.quantity,
        reference_price=event.funding.reference_price,
        funding_rate=event.funding.funding_rate,
    )
    if not isinstance(cost, Decimal) or not cost.is_finite():
        raise ValueError(f"funding model returned an invalid value {cost!r}")
    new_perpetual = replace(
        state.perpetual,
        collateral=state.perpetual.collateral - cost,
        funding_paid=state.perpetual.funding_paid + cost,
    )
    return _checked(
        replace(
            state,
            perpetual=new_perpetual,
            applied_funding_keys=(*state.applied_funding_keys, event.canonical_key),
            last_event_time=event.funding.event_time,
        )
    )


# ---------------------------------------------------------------- valuation


def _checked(state: HedgedPortfolioState) -> HedgedPortfolioState:
    """Ledger-balance invariants, independent of any mark."""
    spot, perp = state.spot, state.perpetual
    if spot.quantity < 0:
        raise ValueError("spot quantity cannot be negative (no spot short in this slice)")
    if perp.quantity > 0:
        raise ValueError("perpetual quantity cannot be positive (no perpetual long in this slice)")
    expected_collateral = (
        state.initial_perpetual_collateral + perp.realized_pnl - perp.costs_paid - perp.funding_paid
    )
    if perp.collateral != expected_collateral:
        raise ValueError(
            f"perpetual collateral {perp.collateral} != initial + realized - costs - funding "
            f"{expected_collateral}"
        )
    return state


def mark_hedged_portfolio(
    state: HedgedPortfolioState,
    *,
    time: datetime,
    spot_mark_price: Decimal,
    perpetual_mark_price: Decimal,
) -> HedgedPortfolioMark:
    """Value both ledgers at the given marks (a view; the state is not changed).

    Fail-fast invariant: equity built from ledger BALANCES minus initial capital
    must equal PnL built from the ATTRIBUTION records (realized + unrealized
    per leg - costs - funding). A double-counted notional, cost or funding
    would make the two disagree.
    """
    state = _require_state(state)
    _require_decimal(spot_mark_price, "spot_mark_price", positive=True)
    _require_decimal(perpetual_mark_price, "perpetual_mark_price", positive=True)
    time_us = datetime_to_epoch_us(time)
    if state.last_event_time is not None and time_us < datetime_to_epoch_us(state.last_event_time):
        raise ValueError(f"mark time {time!r} is before the last event {state.last_event_time!r}")
    spot, perp = state.spot, state.perpetual
    spot_asset_value = spot.quantity * spot_mark_price
    spot_equity = spot.cash + spot_asset_value
    perp_unrealized = (
        Decimal(0)
        if perp.entry_price is None
        else (perpetual_mark_price - perp.entry_price) * perp.quantity
    )
    margin_equity = perp.collateral + perp_unrealized
    portfolio_equity = spot_equity + margin_equity
    total_pnl = portfolio_equity - state.initial_total_capital
    spot_unrealized = (
        Decimal(0)
        if spot.entry_price is None
        else spot.quantity * (spot_mark_price - spot.entry_price)
    )
    attributed = (
        spot.realized_pnl
        + spot_unrealized
        - spot.costs_paid
        + perp.realized_pnl
        + perp_unrealized
        - perp.costs_paid
        - perp.funding_paid
    )
    if attributed != total_pnl:
        raise ValueError(
            f"multi-leg accounting invariant violated: equity - initial = {total_pnl}, "
            f"attributed PnL = {attributed}"
        )
    return HedgedPortfolioMark(
        time=time,
        lifecycle=state.lifecycle,
        spot_mark_price=spot_mark_price,
        perpetual_mark_price=perpetual_mark_price,
        spot_cash=spot.cash,
        spot_quantity=spot.quantity,
        spot_asset_value=spot_asset_value,
        spot_equity=spot_equity,
        perpetual_collateral=perp.collateral,
        perpetual_quantity=perp.quantity,
        perpetual_unrealized_pnl=perp_unrealized,
        perpetual_margin_equity=margin_equity,
        portfolio_equity=portfolio_equity,
        initial_total_capital=state.initial_total_capital,
        total_pnl=total_pnl,
    )


def summarize_closed_hedge(state: HedgedPortfolioState) -> HedgedLifecycleSummary:
    """Final lifecycle summary; final equity is exactly the two wallet balances."""
    state = _require_state(state)
    if state.lifecycle is not HedgeLifecycle.FLAT_CLOSED:
        raise ValueError(f"summary requires lifecycle FLAT_CLOSED, got {state.lifecycle.value}")
    spot, perp = state.spot, state.perpetual
    final_equity = spot.cash + perp.collateral
    total_pnl = final_equity - state.initial_total_capital
    attributed = (
        spot.realized_pnl
        - spot.costs_paid
        + perp.realized_pnl
        - perp.costs_paid
        - perp.funding_paid
    )
    if attributed != total_pnl:
        raise ValueError(
            f"closed hedge invariant violated: final - initial = {total_pnl}, "
            f"attributed PnL = {attributed}"
        )
    return HedgedLifecycleSummary(
        pair_id=state.pair.pair_id,
        fills=state.fills,
        initial_total_capital=state.initial_total_capital,
        final_spot_cash=spot.cash,
        final_perpetual_collateral=perp.collateral,
        final_equity=final_equity,
        spot_realized_pnl=spot.realized_pnl,
        perpetual_realized_pnl=perp.realized_pnl,
        spot_costs=spot.costs_paid,
        perpetual_costs=perp.costs_paid,
        funding_paid=perp.funding_paid,
        total_pnl=total_pnl,
    )
