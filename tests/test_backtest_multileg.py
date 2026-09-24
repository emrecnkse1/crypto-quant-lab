"""First multi-leg accounting slice (FUNDING_RESEARCH_SPEC.md Bölüm 19.10).

Every expected number is hand-derived in the comments; nothing is produced by
calling the code under test.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.multileg import (
    HedgedLifecycleSummary,
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
    summarize_closed_hedge,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.research.decimal_policy import DEFAULT_DECIMAL_CONTEXT, build_context
from crypto_quant_lab.storage.datasets import (
    BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
    BINANCE_USDM_KLINES_SOURCE,
    MARK_PRICE,
    CandleDataset,
    binance_usdm_index_price_dataset,
)

D = Decimal
T0 = datetime(2026, 2, 1, tzinfo=UTC)
H = timedelta(hours=1)
SPOT = TradableInstrument("binance", "spot", "BTCUSDT", "USDT")
PERP = TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT")
PAIR = HedgedPair("btc-spot-perp", SPOT, PERP)
ZERO = ZeroCostModel()
FUNDING = LinearFundingModel()


def _state(spot_cash="200", collateral="200"):
    return new_hedged_portfolio(PAIR, spot_cash=D(spot_cash), perpetual_collateral=D(collateral))


def _leg(instrument, side, price, *, qty="1", time=T0):
    return LegExecution(instrument, side, D(qty), D(price), time)


def _open(state, spot_price, perp_price, *, qty="1", time=T0, spot_cost=ZERO, perp_cost=ZERO):
    return apply_hedged_open(
        state,
        spot=_leg(SPOT, LegSide.BUY, spot_price, qty=qty, time=time),
        perpetual=_leg(PERP, LegSide.SELL, perp_price, qty=qty, time=time),
        spot_cost_model=spot_cost,
        perpetual_cost_model=perp_cost,
    )


def _close(
    state, spot_price, perp_price, *, qty="1", time=T0 + 5 * H, spot_cost=ZERO, perp_cost=ZERO
):
    return apply_hedged_close(
        state,
        spot=_leg(SPOT, LegSide.SELL, spot_price, qty=qty, time=time),
        perpetual=_leg(PERP, LegSide.BUY, perp_price, qty=qty, time=time),
        spot_cost_model=spot_cost,
        perpetual_cost_model=perp_cost,
    )


def _mark(state, spot_price, perp_price, time=T0 + 6 * H):
    return mark_hedged_portfolio(
        state, time=time, spot_mark_price=D(spot_price), perpetual_mark_price=D(perp_price)
    )


def _funding(rate, *, time=T0 + 2 * H, symbol="BTCUSDT", market="usdm_perpetual", ref="100"):
    return HistoricalFundingEvent(
        exchange="binance",
        market_type=market,
        symbol=symbol,
        funding=FundingEvent(
            event_time=time, funding_rate=D(rate), reference_price=D(ref), rate_type="Regular"
        ),
    )


# ================================================================ A-H acceptance


def test_a_open_does_not_double_count_the_perpetual_notional():
    state = _open(_state(), "100", "100")
    mark = _mark(state, "100", "100", time=T0)
    assert (mark.spot_cash, mark.spot_asset_value) == (D(100), D(100))
    assert (mark.perpetual_collateral, mark.perpetual_unrealized_pnl) == (D(200), D(0))
    assert (mark.portfolio_equity, mark.total_pnl) == (D(400), D(0))  # not 500
    assert mark.perpetual_quantity == D(-1)
    assert state.lifecycle is HedgeLifecycle.HEDGED_OPEN


def test_b_both_markets_rise_and_the_hedge_holds():
    mark = _mark(_open(_state(), "100", "100"), "110", "110")
    assert mark.spot_equity == D(210)  # 100 cash + 110
    assert mark.perpetual_unrealized_pnl == D(-10)  # (110 - 100) * -1
    assert mark.perpetual_margin_equity == D(190)
    assert (mark.portfolio_equity, mark.total_pnl) == (D(400), D(0))


def test_c_basis_convergence_realizes_both_legs():
    closed = _close(_open(_state(), "100", "102"), "101", "101")
    summary = summarize_closed_hedge(closed)
    assert summary.spot_realized_pnl == D(1)  # 101 - 100
    assert summary.perpetual_realized_pnl == D(1)  # 102 - 101 on a short
    assert (summary.total_pnl, summary.final_equity) == (D(2), D(402))


def test_d_costs_hit_each_ledger_exactly_once():
    spot_fee = ProportionalCommissionModel(rate=D("0.001"))  # 1 * 100 * 0.001 = 0.1 per fill
    perp_fee = ProportionalCommissionModel(rate=D("0.0005"))  # 1 * 100 * 0.0005 = 0.05 per fill
    opened = _open(_state(), "100", "100", spot_cost=spot_fee, perp_cost=perp_fee)
    assert (opened.spot.cash, opened.perpetual.collateral) == (D("99.9"), D("199.95"))
    closed = _close(opened, "100", "100", spot_cost=spot_fee, perp_cost=perp_fee)
    summary = summarize_closed_hedge(closed)
    assert (summary.spot_costs, summary.perpetual_costs) == (D("0.2"), D("0.1"))
    assert summary.final_spot_cash == D("199.8")  # 200 - 0.1 - 0.1, gross 0
    assert summary.final_perpetual_collateral == D("199.9")  # 200 - 0.05 - 0.05
    assert (summary.final_equity, summary.total_pnl) == (D("399.7"), D("-0.3"))
    assert [(f.spot.cost, f.perpetual.cost) for f in summary.fills] == [
        (D("0.1"), D("0.05")),
        (D("0.1"), D("0.05")),
    ]


@pytest.mark.parametrize(
    ("rate", "paid", "collateral", "equity"),
    [
        ("0.0001", "-0.01", "200.01", "400.01"),  # cost = -1 * 100 * 0.0001: the short receives
        ("-0.0001", "0.01", "199.99", "399.99"),  # cost = -1 * 100 * -0.0001: the short pays
    ],
)
def test_e_funding_moves_only_the_perpetual_ledger_once(rate, paid, collateral, equity):
    opened = _open(_state(), "100", "100")
    funded = apply_perpetual_funding(opened, _funding(rate), funding_model=FUNDING)
    assert funded.perpetual.collateral == D(collateral)
    assert funded.spot == opened.spot
    mark = _mark(funded, "100", "100")
    assert mark.portfolio_equity == D(equity)
    assert funded.perpetual.funding_paid == D(paid)
    assert mark.portfolio_equity - D(400) == -D(paid)  # equity moves by the cashflow, once
    with pytest.raises(ValueError, match="already applied"):
        apply_perpetual_funding(funded, _funding(rate), funding_model=FUNDING)


def test_f_full_close_leaves_only_the_two_wallets():
    closed = _close(_open(_state(), "100", "102"), "101", "101")
    assert (closed.spot.quantity, closed.perpetual.quantity) == (D(0), D(0))
    assert (closed.spot.cash, closed.perpetual.collateral) == (D(201), D(201))
    for spot_mark, perp_mark in (("101", "101"), ("500", "7")):
        mark = _mark(closed, spot_mark, perp_mark)
        assert mark.perpetual_unrealized_pnl == D(0)
        assert mark.portfolio_equity == D(402)  # 201 + 201, no notional at any mark
    summary = summarize_closed_hedge(closed)
    assert (summary.spot_realized_pnl, summary.perpetual_realized_pnl) == (D(1), D(1))
    assert summary.final_equity == summary.final_spot_cash + summary.final_perpetual_collateral
    assert isinstance(summary, HedgedLifecycleSummary) and summary.liquidation_not_modeled


@pytest.mark.parametrize(
    ("spot", "perpetual", "message"),
    [
        (_leg(SPOT, LegSide.BUY, "100"), None, "needs both legs"),
        (None, _leg(PERP, LegSide.SELL, "100"), "needs both legs"),
        (
            _leg(SPOT, LegSide.BUY, "100", qty="1"),
            _leg(PERP, LegSide.SELL, "100", qty="0.5"),
            "equal base quantity",
        ),
        (
            _leg(SPOT, LegSide.BUY, "100"),
            _leg(PERP, LegSide.SELL, "100", time=T0 + H),
            "share one timestamp",
        ),
    ],
)
def test_g_invalid_pairs_are_rejected_atomically(spot, perpetual, message):
    state = _state()
    before = (state.spot, state.perpetual, state.fills, state.lifecycle)
    with pytest.raises(ValueError, match=message):
        apply_hedged_open(
            state, spot=spot, perpetual=perpetual, spot_cost_model=ZERO, perpetual_cost_model=ZERO
        )
    assert (state.spot, state.perpetual, state.fills, state.lifecycle) == before
    assert state.fills == () and state.spot.cash == D(200) and state.perpetual.collateral == D(200)


def test_h_direction_ratio_and_tradability_limits():
    state = _state()
    with pytest.raises(ValueError, match="requires spot BUY"):  # spot short
        apply_hedged_open(
            state,
            spot=_leg(SPOT, LegSide.SELL, "100"),
            perpetual=_leg(PERP, LegSide.SELL, "100"),
            spot_cost_model=ZERO,
            perpetual_cost_model=ZERO,
        )
    with pytest.raises(ValueError, match="requires perpetual SELL"):  # perpetual long
        apply_hedged_open(
            state,
            spot=_leg(SPOT, LegSide.BUY, "100"),
            perpetual=_leg(PERP, LegSide.BUY, "100"),
            spot_cost_model=ZERO,
            perpetual_cost_model=ZERO,
        )
    with pytest.raises(ValueError, match="quantity must be > 0"):
        _leg(SPOT, LegSide.BUY, "100", qty="-1")
    with pytest.raises(ValueError, match="1:1"):
        HedgedPair("x", SPOT, PERP, hedge_ratio=D(2))
    index = binance_usdm_index_price_dataset("BTCUSDT", "1h")
    mark = CandleDataset("binance", "usdm_perpetual", "BTCUSDT", "1h", MARK_PRICE, "binance:mark")
    for dataset in (index, mark):
        with pytest.raises(ValueError, match="not a tradable leg"):
            TradableInstrument.from_candle_dataset(dataset, quote_asset="USDT")
    contract = CandleDataset(
        "binance", "usdm_perpetual", "BTCUSDT", "1h", "contract_trade", BINANCE_USDM_KLINES_SOURCE
    )
    assert TradableInstrument.from_candle_dataset(contract, quote_asset="USDT") == PERP
    assert BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE != BINANCE_USDM_KLINES_SOURCE


# ================================================================ additional coverage


def test_models_are_frozen_and_validate_genuine_finite_positive_decimals():
    with pytest.raises(FrozenInstanceError):
        SPOT.symbol = "ETHUSDT"
    for bad, error in (
        (100, TypeError),
        (100.0, TypeError),
        (True, TypeError),
        (D("NaN"), ValueError),
        (D("Infinity"), ValueError),
        (D(0), ValueError),
    ):
        with pytest.raises(error):
            LegExecution(SPOT, LegSide.BUY, D(1), bad, T0)
    with pytest.raises(ValueError, match="timezone-aware"):
        LegExecution(SPOT, LegSide.BUY, D(1), D(1), datetime(2026, 2, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="market_type"):
        TradableInstrument("binance", "coinm", "BTCUSD", "USD")
    with pytest.raises(ValueError, match="quote asset"):
        HedgedPair("x", SPOT, TradableInstrument("binance", "usdm_perpetual", "BTCUSDC", "USDC"))
    with pytest.raises(ValueError, match="spot leg must have market_type"):
        HedgedPair("x", PERP, PERP)
    with pytest.raises(ValueError, match="spot_cash must be >= 0"):
        new_hedged_portfolio(PAIR, spot_cash=D(-1), perpetual_collateral=D(1))
    assert _state() == _state()


def test_wallets_are_separate_and_spot_cash_must_suffice():
    with pytest.raises(ValueError, match="insufficient spot cash"):
        _open(_state(spot_cash="99.99", collateral="1000"), "100", "100")
    # a large perpetual collateral never funds the spot purchase
    state = _open(_state(spot_cash="100", collateral="1"), "100", "100")
    assert (state.spot.cash, state.perpetual.collateral) == (D(0), D(1))


def test_invalid_cost_model_outputs_are_rejected():
    class Returns:
        def __init__(self, value):
            self.value = value

        def calculate_cost(self, *, quantity, execution_price):
            return self.value

    for value, error in ((0.1, TypeError), (D(-1), ValueError), (D("NaN"), ValueError)):
        state = _state()
        with pytest.raises(error):
            _open(state, "100", "100", perp_cost=Returns(value))
        assert state.fills == ()


def test_lifecycle_order_and_time_rules():
    state = _state()
    with pytest.raises(ValueError, match="close requires lifecycle HEDGED_OPEN"):
        _close(state, "100", "100")
    with pytest.raises(ValueError, match="funding needs an open perpetual leg"):
        apply_perpetual_funding(state, _funding("0.0001"), funding_model=FUNDING)
    opened = _open(state, "100", "100")
    with pytest.raises(ValueError, match="open requires lifecycle FLAT"):
        _open(opened, "100", "100", time=T0 + H)
    with pytest.raises(ValueError, match="no partial close"):
        _close(opened, "100", "100", qty="0.5")
    with pytest.raises(ValueError, match="strictly after"):
        _close(opened, "100", "100", time=T0)  # same instant as the open
    with pytest.raises(ValueError, match="strictly after"):  # funding at the open instant
        apply_perpetual_funding(opened, _funding("0.0001", time=T0), funding_model=FUNDING)
    funded = apply_perpetual_funding(
        opened, _funding("0.0001", time=T0 + 5 * H), funding_model=FUNDING
    )
    with pytest.raises(ValueError, match="strictly after"):  # close at the funding instant
        _close(funded, "100", "100", time=T0 + 5 * H)
    with pytest.raises(ValueError, match="before the last event"):
        _mark(funded, "100", "100", time=T0 + 4 * H)
    closed = _close(funded, "100", "100", time=T0 + 6 * H)
    with pytest.raises(ValueError, match="funding needs an open perpetual leg"):
        apply_perpetual_funding(closed, _funding("0.0001", time=T0 + 7 * H), funding_model=FUNDING)
    with pytest.raises(ValueError, match="summary requires lifecycle FLAT_CLOSED"):
        summarize_closed_hedge(funded)


def test_equivalent_timezone_instants_are_one_instant():
    plus3 = timezone(timedelta(hours=3))
    opened = _open(_state(), "100", "100")
    with pytest.raises(ValueError, match="strictly after"):
        _close(opened, "100", "100", time=T0.astimezone(plus3))


def test_foreign_funding_events_are_rejected():
    opened = _open(_state(), "100", "100")
    for event in (_funding("0.0001", symbol="ETHUSDT"), _funding("0.0001", market="spot")):
        with pytest.raises(ValueError, match="not for the pair's perpetual leg"):
            apply_perpetual_funding(opened, event, funding_model=FUNDING)


def test_different_leg_moves_basis_widening_and_narrowing():
    opened = _open(_state(), "100", "101")  # basis +1
    # widening: spot 105, perp 108 (basis 3) -> spot +5, perp (108 - 101) * -1 = -7 -> -2
    widened = _mark(opened, "105", "108")
    assert (widened.total_pnl, widened.portfolio_equity) == (D(-2), D(398))
    # narrowing: spot 105, perp 105 (basis 0) -> +5 and -4 -> +1
    narrowed = _mark(opened, "105", "105")
    assert (narrowed.total_pnl, narrowed.perpetual_unrealized_pnl) == (D(1), D(-4))
    assert narrowed.spot_equity - D(200) == D(5)  # realized/unrealized kept apart: spot unrealized


def test_negative_margin_equity_is_shown_not_liquidated():
    opened = _open(_state(spot_cash="200", collateral="5"), "100", "100")
    mark = _mark(opened, "120", "120")
    assert mark.perpetual_unrealized_pnl == D(-20)
    assert mark.perpetual_margin_equity == D(-15)  # 5 - 20, shown as is
    assert mark.portfolio_equity == D(205)  # spot 100 + 120, perp -15; hedge PnL 0
    assert mark.liquidation_not_modeled and opened.liquidation_not_modeled


def test_transitions_are_pure_and_repeatable():
    state = _state()
    spot = _leg(SPOT, LegSide.BUY, "100")
    perp = _leg(PERP, LegSide.SELL, "100")
    first = apply_hedged_open(
        state, spot=spot, perpetual=perp, spot_cost_model=ZERO, perpetual_cost_model=ZERO
    )
    second = apply_hedged_open(
        state, spot=spot, perpetual=perp, spot_cost_model=ZERO, perpetual_cost_model=ZERO
    )
    assert first == second and first is not state
    assert state.lifecycle is HedgeLifecycle.FLAT and state.fills == ()
    assert spot == _leg(SPOT, LegSide.BUY, "100")


def test_explicit_research_context_makes_results_independent_of_ambient_context():
    def scenario():
        fee = ProportionalCommissionModel(rate=D("0.00075"))
        opened = _open(
            _state("20000", "20000"), "12345.678901", "12400.123456", spot_cost=fee, perp_cost=fee
        )
        funded = apply_perpetual_funding(
            opened, _funding("0.000123", ref="12399.987654"), funding_model=FUNDING
        )
        closed = _close(funded, "12500.5", "12501.25", spot_cost=fee, perp_cost=fee)
        return summarize_closed_hedge(closed)

    with localcontext(build_context(DEFAULT_DECIMAL_CONTEXT)):
        reference = scenario()
    with localcontext() as ambient:
        ambient.prec = 4
        with localcontext(build_context(DEFAULT_DECIMAL_CONTEXT)):
            assert scenario() == reference
        with pytest.raises(ValueError, match="closed hedge invariant violated"):
            scenario()  # 4-digit ambient arithmetic: final - initial 1E+1 vs attributed 17.94
    # hand check of the ledger: spot 20000 - 12345.678901 - fee, fee = 12345.678901 * 0.00075
    assert reference.final_spot_cash == (
        D("20000") - D("12345.678901") - D("9.25925917575") + D("12500.5") - D("9.375375")
    )
