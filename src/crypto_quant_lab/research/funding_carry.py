"""First Faz 7 vertical slice: a leakage-safe funding-carry research policy (FUNDING_RESEARCH_SPEC.md).

Research question: does the most recently SETTLED funding rate that is actually
known at a decision instant carry usable information about the forward return
of holding the perpetual, after transaction costs and after the realized
funding payments the position itself settles?

Direction is taken from Binance's documented settlement semantics, not assumed:
a positive rate means longs pay shorts, a negative rate means shorts pay longs.
`FundingCarryPolicy` therefore holds SHORT when the last settled rate is at or
above `short_entry_rate`, LONG when it is at or below `long_entry_rate`, and FLAT
otherwise — a hypothesis to be tested, not a claim of profitability.

Funding stays two separate things here:
- signal input: `FundingSignalHistory` exposes a settled event only once
  `event_time + publication_lag <= decision time` (the repository's inclusive
  availability boundary); raw future events are never exposed;
- cash accounting: done exclusively by the existing replay/store runner
  (`funding_required=True`); nothing in this module touches cash or equity.

No basis (no synchronous spot + perpetual price source exists in the engine),
no predicted/estimated funding (only settled history is stored), no optimizer,
no best-candidate selection, no orders, no risk decisions.
"""

from bisect import bisect_right as _bisect_right
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.policy import BacktestPolicy, PolicyContext
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.quality import build_funding_data_quality_report_from_store
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.storage.base import HistoricalCandleStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.rolling import run_rolling_backtest_from_store
from crypto_quant_lab.validation.windows import TemporalWindow

FUNDING_CARRY_STRATEGY = "funding_carry_v1"
NO_TRADE_CONTROL_STRATEGY = "no_trade_control"

_FUNDING_CARRY_KEYS = (
    "long_entry_rate",
    "max_funding_age_us",
    "publication_lag_us",
    "short_entry_rate",
    "strategy",
)
_NO_TRADE_KEYS = ("strategy",)


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value


def _require_timedelta(value: object, field_name: str, *, allow_zero: bool) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta, got {type(value).__name__}")
    if value < timedelta(0) or (not allow_zero and value == timedelta(0)):
        bound = ">= 0" if allow_zero else "> 0"
        raise ValueError(f"{field_name} must be {bound}, got {value!r}")
    return value


def _require_rate(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value}")
    return value


def _timedelta_us(value: timedelta) -> int:
    return value // timedelta(microseconds=1)


class FundingSignalHistory:
    """Settled funding history of one partition, readable only as of a decision instant.

    Constructed from a fully covered `[coverage_start, coverage_end)` history.
    Events must be strictly ascending by `event_time` inside the coverage range:
    duplicates and several events at one instant (e.g. different `rate_type`s)
    are rejected, because a single signal value would be ambiguous. There is
    deliberately no public accessor for the raw event sequence.
    """

    __slots__ = (
        "_coverage_end",
        "_coverage_start",
        "_event_times_us",
        "_events",
        "_exchange",
        "_market_type",
        "_publication_lag",
        "_symbol",
    )

    def __init__(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        coverage_start: datetime,
        coverage_end: datetime,
        publication_lag: timedelta,
        events: tuple[FundingEvent, ...],
    ) -> None:
        exchange = _require_identifier(exchange, "exchange")
        market_type = _require_identifier(market_type, "market_type")
        symbol = _require_identifier(symbol, "symbol")
        start_us = datetime_to_epoch_us(coverage_start)
        end_us = datetime_to_epoch_us(coverage_end)
        if start_us >= end_us:
            raise ValueError(
                "coverage_start must be strictly before coverage_end, got "
                f"coverage_start={coverage_start!r}, coverage_end={coverage_end!r}"
            )
        publication_lag = _require_timedelta(publication_lag, "publication_lag", allow_zero=True)
        if not isinstance(events, tuple):
            raise TypeError(f"events must be a tuple, got {type(events).__name__}")
        for index, event in enumerate(events):
            if not isinstance(event, FundingEvent):
                raise TypeError(
                    f"events[{index}] must be a FundingEvent, got {type(event).__name__}"
                )
        event_times_us = []
        for index, event in enumerate(events):
            event_us = datetime_to_epoch_us(event.event_time)
            if not start_us <= event_us < end_us:
                raise ValueError(
                    f"events[{index}].event_time ({event.event_time!r}) is outside coverage "
                    f"[{coverage_start!r}, {coverage_end!r})"
                )
            if event_times_us and event_us <= event_times_us[-1]:
                raise ValueError(
                    f"events[{index}].event_time ({event.event_time!r}) must be strictly after "
                    f"events[{index - 1}].event_time; duplicate or unordered funding events "
                    "are not a single unambiguous signal"
                )
            event_times_us.append(event_us)

        object.__setattr__(self, "_exchange", exchange)
        object.__setattr__(self, "_market_type", market_type)
        object.__setattr__(self, "_symbol", symbol)
        object.__setattr__(self, "_coverage_start", coverage_start)
        object.__setattr__(self, "_coverage_end", coverage_end)
        object.__setattr__(self, "_publication_lag", publication_lag)
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "_event_times_us", tuple(event_times_us))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FundingSignalHistory is immutable")

    @property
    def exchange(self) -> str:
        return self._exchange

    @property
    def market_type(self) -> str:
        return self._market_type

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def coverage_start(self) -> datetime:
        return self._coverage_start

    @property
    def coverage_end(self) -> datetime:
        return self._coverage_end

    @property
    def publication_lag(self) -> timedelta:
        return self._publication_lag

    def latest_settled_at(self, as_of_time: datetime) -> FundingEvent | None:
        """The last settled event with `event_time + publication_lag <= as_of_time`, else None.

        The knowledge cutoff `as_of_time - publication_lag` must lie inside the
        covered range `[coverage_start, coverage_end)`; otherwise the history
        cannot tell whether an unseen settlement exists, and a ValueError is
        raised instead of guessing.
        """
        as_of_us = datetime_to_epoch_us(as_of_time)
        cutoff_us = as_of_us - _timedelta_us(self._publication_lag)
        if cutoff_us < datetime_to_epoch_us(self._coverage_start):
            raise ValueError(
                f"knowledge cutoff for as_of_time={as_of_time!r} is before funding coverage "
                f"start {self._coverage_start!r}"
            )
        if cutoff_us >= datetime_to_epoch_us(self._coverage_end):
            raise ValueError(
                f"knowledge cutoff for as_of_time={as_of_time!r} is not before funding coverage "
                f"end {self._coverage_end!r}"
            )
        position = _bisect_right(self._event_times_us, cutoff_us)
        return self._events[position - 1] if position else None


def load_funding_signal_history(
    funding_store: HistoricalFundingStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    coverage_start: datetime,
    coverage_end: datetime,
    publication_lag: timedelta,
) -> FundingSignalHistory:
    """Quality-gate and load settled funding history over `[coverage_start, coverage_end)`.

    The existing funding quality report must PASS (complete coverage union);
    otherwise a ValueError is raised — gaps are never hidden or filled.
    """
    report = build_funding_data_quality_report_from_store(
        funding_store,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        requested_start=coverage_start,
        requested_end=coverage_end,
    )
    if report.overall_status != "PASS":
        raise ValueError(
            f"funding signal history quality gate failed: overall_status="
            f"{report.overall_status!r}, coverage_gap_count={report.coverage_gap_count}, "
            f"coverage_gaps={report.coverage_gaps!r}"
        )
    events = funding_store.query_events(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        start_time=coverage_start,
        end_time=coverage_end,
    )
    payloads = []
    for event in events:
        if not isinstance(event, HistoricalFundingEvent):
            raise TypeError(
                "funding_store.query_events must return HistoricalFundingEvent instances, "
                f"got {type(event).__name__}"
            )
        if (event.exchange, event.market_type, event.symbol) != (exchange, market_type, symbol):
            raise ValueError(
                "queried funding event partition does not match the requested partition: "
                f"got ({event.exchange!r}, {event.market_type!r}, {event.symbol!r})"
            )
        payloads.append(event.funding)
    return FundingSignalHistory(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        publication_lag=publication_lag,
        events=tuple(payloads),
    )


class FundingCarryPolicy:
    """SHORT at/above `short_entry_rate`, LONG at/below `long_entry_rate`, FLAT otherwise.

    FLAT is also the explicit no-trade outcome when no settled event is known yet
    or the latest known event is older than `max_funding_age` — a missing or
    stale rate is never treated as zero. Stateless: each decision depends only
    on `context.as_of_time` and the time-gated history.
    """

    __slots__ = ("_history", "_long_entry_rate", "_max_funding_age", "_short_entry_rate")

    def __init__(
        self,
        history: FundingSignalHistory,
        *,
        short_entry_rate: Decimal,
        long_entry_rate: Decimal,
        max_funding_age: timedelta,
    ) -> None:
        if not isinstance(history, FundingSignalHistory):
            raise TypeError(f"history must be a FundingSignalHistory, got {type(history).__name__}")
        short_entry_rate = _require_rate(short_entry_rate, "short_entry_rate")
        long_entry_rate = _require_rate(long_entry_rate, "long_entry_rate")
        if not long_entry_rate < short_entry_rate:
            raise ValueError(
                "long_entry_rate must be strictly below short_entry_rate, got "
                f"long_entry_rate={long_entry_rate}, short_entry_rate={short_entry_rate}"
            )
        max_funding_age = _require_timedelta(max_funding_age, "max_funding_age", allow_zero=False)
        self._history = history
        self._short_entry_rate = short_entry_rate
        self._long_entry_rate = long_entry_rate
        self._max_funding_age = max_funding_age

    def target_position(self, context: PolicyContext) -> PositionTarget:
        if not isinstance(context, PolicyContext):
            raise TypeError(f"context must be a PolicyContext, got {type(context).__name__}")
        for candle in context.candles:
            if candle.symbol != self._history.symbol:
                raise ValueError(
                    f"context candle symbol {candle.symbol!r} does not match funding history "
                    f"symbol {self._history.symbol!r}"
                )
        event = self._history.latest_settled_at(context.as_of_time)
        if event is None or context.as_of_time - event.event_time > self._max_funding_age:
            return PositionTarget.FLAT
        if event.funding_rate >= self._short_entry_rate:
            return PositionTarget.SHORT
        if event.funding_rate <= self._long_entry_rate:
            return PositionTarget.LONG
        return PositionTarget.FLAT


class NoTradeControlPolicy:
    """Control arm: always FLAT, so it never trades and never settles funding."""

    __slots__ = ()

    def target_position(self, context: PolicyContext) -> PositionTarget:
        if not isinstance(context, PolicyContext):
            raise TypeError(f"context must be a PolicyContext, got {type(context).__name__}")
        return PositionTarget.FLAT


def funding_carry_candidate(
    candidate_id: str,
    *,
    short_entry_rate: Decimal,
    long_entry_rate: Decimal,
    max_funding_age: timedelta,
    publication_lag: timedelta,
) -> Candidate:
    """A `Candidate` whose parameters fully determine a `FundingCarryPolicy` configuration."""
    short_entry_rate = _require_rate(short_entry_rate, "short_entry_rate")
    long_entry_rate = _require_rate(long_entry_rate, "long_entry_rate")
    if not long_entry_rate < short_entry_rate:
        raise ValueError(
            "long_entry_rate must be strictly below short_entry_rate, got "
            f"long_entry_rate={long_entry_rate}, short_entry_rate={short_entry_rate}"
        )
    max_funding_age = _require_timedelta(max_funding_age, "max_funding_age", allow_zero=False)
    publication_lag = _require_timedelta(publication_lag, "publication_lag", allow_zero=True)
    return Candidate(
        candidate_id=candidate_id,
        parameters=(
            ("long_entry_rate", long_entry_rate),
            ("max_funding_age_us", _timedelta_us(max_funding_age)),
            ("publication_lag_us", _timedelta_us(publication_lag)),
            ("short_entry_rate", short_entry_rate),
            ("strategy", FUNDING_CARRY_STRATEGY),
        ),
    )


def no_trade_control_candidate(candidate_id: str) -> Candidate:
    """A `Candidate` for the always-FLAT control arm."""
    return Candidate(
        candidate_id=candidate_id, parameters=(("strategy", NO_TRADE_CONTROL_STRATEGY),)
    )


def funding_research_policy_factory(
    candidate: Candidate, history: FundingSignalHistory
) -> Callable[[], BacktestPolicy]:
    """A fresh-instance-per-call policy factory driven only by `candidate.parameters`.

    For the carry strategy the candidate's `publication_lag_us` must equal the
    history's publication lag, so the recorded configuration is the one used.
    """
    if not isinstance(candidate, Candidate):
        raise TypeError(f"candidate must be a Candidate, got {type(candidate).__name__}")
    if not isinstance(history, FundingSignalHistory):
        raise TypeError(f"history must be a FundingSignalHistory, got {type(history).__name__}")
    parameters = dict(candidate.parameters)
    strategy = parameters.get("strategy")
    if strategy == NO_TRADE_CONTROL_STRATEGY:
        expected_keys = _NO_TRADE_KEYS
    elif strategy == FUNDING_CARRY_STRATEGY:
        expected_keys = _FUNDING_CARRY_KEYS
    else:
        raise ValueError(f"unsupported research strategy parameter: {strategy!r}")
    if tuple(parameters) != expected_keys:
        raise ValueError(
            f"candidate {candidate.candidate_id!r} parameters must be exactly {expected_keys}, "
            f"got {tuple(parameters)}"
        )
    if strategy == NO_TRADE_CONTROL_STRATEGY:
        return NoTradeControlPolicy

    lag_us = parameters["publication_lag_us"]
    age_us = parameters["max_funding_age_us"]
    for name, value in (("publication_lag_us", lag_us), ("max_funding_age_us", age_us)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if lag_us != _timedelta_us(history.publication_lag):
        raise ValueError(
            f"candidate publication_lag_us={lag_us} does not match history publication_lag "
            f"{history.publication_lag!r}"
        )
    short_entry_rate = parameters["short_entry_rate"]
    long_entry_rate = parameters["long_entry_rate"]
    max_funding_age = timedelta(microseconds=age_us)

    def factory() -> FundingCarryPolicy:
        return FundingCarryPolicy(
            history,
            short_entry_rate=short_entry_rate,
            long_entry_rate=long_entry_rate,
            max_funding_age=max_funding_age,
        )

    return factory


def evaluate_funding_research_candidate(
    candle_store: HistoricalCandleStore,
    funding_store: HistoricalFundingStore,
    history: FundingSignalHistory,
    candidate: Candidate,
    *,
    windows: tuple[TemporalWindow, ...],
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_model: FundingModel,
) -> Trial:
    """Rolling evaluation of one research candidate on the history's own partition.

    Delegates to the existing `run_rolling_backtest_from_store` with
    `funding_required=True`, so transaction costs and realized funding are
    each applied exactly once by the canonical engine. Returns the `Trial`
    evidence; metrics are computed by the caller with the existing functions.
    """
    policy_factory = funding_research_policy_factory(candidate, history)
    results = run_rolling_backtest_from_store(
        candle_store,
        windows,
        policy_factory=policy_factory,
        exchange=history.exchange,
        market_type=history.market_type,
        symbol=history.symbol,
        timeframe=timeframe,
        as_of_time=as_of_time,
        config=config,
        cost_model=cost_model,
        funding_required=True,
        funding_store=funding_store,
        funding_model=funding_model,
    )
    return Trial(
        candidate=candidate,
        results=results,
        exchange=history.exchange,
        market_type=history.market_type,
        symbol=history.symbol,
        timeframe=timeframe,
        as_of_time=as_of_time,
        config=config,
    )
