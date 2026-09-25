"""E1: opt-in FUNDING_BEFORE_FILL event ordering (FUNDING_RESEARCH_SPEC.md Bölüm 19.12.4).

The first-slice tests (test_backtest_multileg.py) stay untouched and prove the
default STRICT_TIME behavior; this file proves the opt-in ordering and that it
cannot be used to reorder history. Expected numbers are hand-derived.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.multileg import (
    EventOrdering,
    HedgedPair,
    HedgeLifecycle,
    LegExecution,
    LegSide,
    TradableInstrument,
    apply_hedged_close,
    apply_hedged_open,
    apply_perpetual_funding,
    mark_hedged_portfolio,
    new_hedged_portfolio,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent

D = Decimal
T0 = datetime(2026, 1, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
SPOT = TradableInstrument("binance", "spot", "BTCUSDT", "USDT")
PERP = TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT")
PAIR = HedgedPair("p", SPOT, PERP)
ZERO = ZeroCostModel()
FUNDING = LinearFundingModel()
ORDERED = EventOrdering.FUNDING_BEFORE_FILL


def _state(ordering=ORDERED):
    return new_hedged_portfolio(
        PAIR, spot_cash=D(200), perpetual_collateral=D(200), event_ordering=ordering
    )


def _open(state, time=T0):
    return apply_hedged_open(
        state,
        spot=LegExecution(SPOT, LegSide.BUY, D(1), D(100), time),
        perpetual=LegExecution(PERP, LegSide.SELL, D(1), D(102), time),
        spot_cost_model=ZERO,
        perpetual_cost_model=ZERO,
    )


def _close(state, time):
    return apply_hedged_close(
        state,
        spot=LegExecution(SPOT, LegSide.SELL, D(1), D(101), time),
        perpetual=LegExecution(PERP, LegSide.BUY, D(1), D(101), time),
        spot_cost_model=ZERO,
        perpetual_cost_model=ZERO,
    )


def _funding(time, rate="0.0001", rate_type="Regular"):
    return HistoricalFundingEvent(
        exchange="binance",
        market_type="usdm_perpetual",
        symbol="BTCUSDT",
        funding=FundingEvent(
            event_time=time, funding_rate=D(rate), reference_price=D(101), rate_type=rate_type
        ),
    )


def test_default_ordering_is_strict_time_and_unchanged():
    legacy = new_hedged_portfolio(PAIR, spot_cash=D(200), perpetual_collateral=D(200))
    assert legacy.event_ordering is EventOrdering.STRICT_TIME
    opened = _open(legacy)
    funded = apply_perpetual_funding(opened, _funding(T0 + 2 * H), funding_model=FUNDING)
    with pytest.raises(ValueError, match="strictly after"):
        _close(funded, T0 + 2 * H)


def test_funding_then_close_at_one_instant_settles_the_held_short():
    opened = _open(_state())
    funded = apply_perpetual_funding(opened, _funding(T0 + 2 * H), funding_model=FUNDING)
    closed = _close(funded, T0 + 2 * H)
    # funding cost = -1 * 101 * 0.0001 = -0.0101 (short receives), then +1 realized on the perp
    assert funded.perpetual.funding_paid == D("-0.0101")
    assert closed.perpetual.collateral == D("201.0101")
    assert closed.spot.cash == D(201)
    assert closed.lifecycle is HedgeLifecycle.FLAT_CLOSED
    assert closed.event_ordering is ORDERED  # transitions never change the mode


def test_fill_then_funding_at_the_same_instant_is_refused():
    opened = _open(_state())
    closed = _close(opened, T0 + 2 * H)
    with pytest.raises(ValueError, match="funding needs an open perpetual leg"):
        apply_perpetual_funding(closed, _funding(T0 + 2 * H), funding_model=FUNDING)
    # an OPEN state whose last event is a fill at T refuses funding at T (would precede the fill)
    with pytest.raises(ValueError, match="violates FUNDING_BEFORE_FILL ordering"):
        apply_perpetual_funding(opened, _funding(T0), funding_model=FUNDING)


def test_several_fundings_at_one_instant_follow_rate_type_order():
    opened = _open(_state())
    t = T0 + H
    first = apply_perpetual_funding(opened, _funding(t, "0.0001", "Regular"), funding_model=FUNDING)
    second = apply_perpetual_funding(
        first, _funding(t, "0.00005", "Special"), funding_model=FUNDING
    )
    # -0.0101 and -0.00505 settled separately
    assert second.perpetual.funding_paid == D("-0.01515")
    assert len(second.applied_funding_keys) == 2
    with pytest.raises(ValueError, match="violates FUNDING_BEFORE_FILL ordering"):
        apply_perpetual_funding(first, _funding(t, "0.0001", "Aaa"), funding_model=FUNDING)
    with pytest.raises(ValueError, match="already applied"):
        apply_perpetual_funding(second, _funding(t, "0.0001", "Regular"), funding_model=FUNDING)


def test_earlier_times_stay_refused_and_later_times_behave_as_before():
    opened = _open(_state())
    with pytest.raises(ValueError, match="violates"):
        apply_perpetual_funding(opened, _funding(T0 - H), funding_model=FUNDING)
    with pytest.raises(ValueError, match="violates"):
        _close(opened, T0)  # a second fill at the open instant
    later = apply_perpetual_funding(opened, _funding(T0 + 3 * H), funding_model=FUNDING)
    assert later.last_event_time == T0 + 3 * H
    with pytest.raises(ValueError, match="violates"):
        _close(later, T0 + H)


def test_manual_state_without_consistent_history_is_refused():
    opened = _open(_state())
    tampered = replace(opened, last_event_time=T0 + 5 * H)  # cursor no longer matches history
    with pytest.raises(ValueError, match="cannot be inferred"):
        apply_perpetual_funding(tampered, _funding(T0 + 6 * H), funding_model=FUNDING)
    forgot = replace(opened, fills=())
    with pytest.raises(ValueError, match="cannot be inferred"):
        _close(forgot, T0 + H)


def test_event_ordering_is_validated_and_marks_are_views():
    with pytest.raises(TypeError, match="EventOrdering"):
        replace(_state(), event_ordering="FUNDING_BEFORE_FILL")
    opened = _open(_state())
    funded = apply_perpetual_funding(opened, _funding(T0 + 2 * H), funding_model=FUNDING)
    # pre-fill mark at the funding instant: spot 100 + 101, perp 200.0101 + (101 - 102) * -1
    mark = mark_hedged_portfolio(
        funded, time=T0 + 2 * H, spot_mark_price=D(101), perpetual_mark_price=D(101)
    )
    assert mark.portfolio_equity == D("402.0101")
    assert _close(funded, T0 + 2 * H).last_event_time == T0 + 2 * H


def test_failed_ordered_transition_leaves_state_untouched():
    opened = _open(_state())
    before = (opened.spot, opened.perpetual, opened.fills, opened.applied_funding_keys)
    with pytest.raises(ValueError, match="violates FUNDING_BEFORE_FILL ordering"):
        apply_perpetual_funding(opened, _funding(T0), funding_model=FUNDING)
    assert (opened.spot, opened.perpetual, opened.fills, opened.applied_funding_keys) == before
