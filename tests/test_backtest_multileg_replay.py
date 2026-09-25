"""In-memory multi-leg replay acceptance (FUNDING_RESEARCH_SPEC.md Bölüm 19.12.5 S1–S12, N1–N7).

Every expected number is hand-derived in the comments; nothing is produced by
calling the code under test.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.multileg import HedgedPair, HedgeLifecycle, TradableInstrument
from crypto_quant_lab.backtest.multileg_replay import (
    HedgeAction,
    HedgeIntent,
    LegCandles,
    TraceKind,
    run_multileg_replay,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.datasets import BINANCE_USDM_KLINES_SOURCE, CandleDataset

D = Decimal
H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1, T2, T3, T4 = (T0 + k * H for k in range(1, 5))
SPOT = TradableInstrument("binance", "spot", "BTCUSDT", "USDT")
PERP = TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT")
PAIR = HedgedPair("btc", SPOT, PERP)
SPOT_DS = CandleDataset("binance", "spot", "BTCUSDT", "1h", "spot_trade", "binance:spot-klines")
PERP_DS = CandleDataset(
    "binance", "usdm_perpetual", "BTCUSDT", "1h", "contract_trade", BINANCE_USDM_KLINES_SOURCE
)
ZERO = ZeroCostModel()


class FixedCost:
    """Test double: a fixed cost per fill (hand-checkable)."""

    def __init__(self, amount: str) -> None:
        self.amount = D(amount)

    def calculate_cost(self, *, quantity, execution_price):
        return self.amount


def candle(symbol, k, open_, close):
    o, c = D(open_), D(close)
    return Candle(symbol, "1h", T0 + k * H, o, max(o, c), min(o, c), c, D(1))


# N fixture (user-specified): spot open/close, perpetual open/close per hour
N_SPOT = (("100", "100"), ("100", "101"), ("101", "101"), ("101", "101"))
N_PERP = (("102", "102"), ("102", "102"), ("102", "101"), ("101", "101"))
# S fixture (§19.12.5): closes as specified; opens of mum 0 and 2 chosen equal to the
# previous close (they are never fill prices)
S_SPOT = (("100", "100"), ("100", "100.5"), ("100.5", "101"), ("101", "101"))
S_PERP = (("102", "102"), ("102", "101.5"), ("101.5", "101"), ("101", "101"))


def legs(spot_rows=N_SPOT, perp_rows=N_PERP):
    return (
        LegCandles(SPOT_DS, tuple(candle("BTCUSDT", k, *r) for k, r in enumerate(spot_rows))),
        LegCandles(PERP_DS, tuple(candle("BTCUSDT", k, *r) for k, r in enumerate(perp_rows))),
    )


def intents(open_at=T1, close_at=T3, qty="1"):
    script = [HedgeIntent(HedgeAction.OPEN, open_at, D(qty))]
    if close_at is not None:
        script.append(HedgeIntent(HedgeAction.CLOSE, close_at, D(qty)))
    return tuple(script)


def funding(time, rate, rate_type="Regular", ref="101"):
    return HistoricalFundingEvent(
        exchange="binance",
        market_type="usdm_perpetual",
        symbol="BTCUSDT",
        funding=FundingEvent(
            event_time=time, funding_rate=D(rate), reference_price=D(ref), rate_type=rate_type
        ),
    )


def run(*, fundings=(), script=None, spot_cost=ZERO, perp_cost=ZERO, rows=None, as_of=T4):
    spot, perp = legs(*(rows or (N_SPOT, N_PERP)))
    return run_multileg_replay(
        pair=PAIR,
        spot=spot,
        perpetual=perp,
        funding_events=tuple(fundings),
        intents=intents() if script is None else script,
        spot_cash=D(200),
        perpetual_collateral=D(200),
        spot_cost_model=spot_cost,
        perpetual_cost_model=perp_cost,
        funding_model=LinearFundingModel(),
        as_of_time=as_of,
    )


def equities(result):
    return [p.mark.portfolio_equity for p in result.equity_points]


# ================================================================ N1–N7


def test_n1_no_funding_no_costs():
    result = run()
    # T1 FLAT 400; T2 spot 100 + 101, perp 200 + (102 - 102)(-1); T3 201 + 201; T4 201 + 201
    assert equities(result) == [D(400), D(401), D(402), D(402)]
    assert [p.mark.time for p in result.equity_points] == [T1, T2, T3, T4]
    final = result.final_state
    assert (final.spot.cash, final.perpetual.collateral) == (D(201), D(201))
    assert (final.spot.quantity, final.perpetual.quantity) == (D(0), D(0))
    assert (final.spot.realized_pnl, final.perpetual.realized_pnl) == (D(1), D(1))
    assert (result.final_mark.portfolio_equity, result.final_mark.total_pnl) == (D(402), D(2))
    assert [f.time for f in result.paired_fills] == [T1, T3]
    assert [(f.spot.execution.price, f.perpetual.execution.price) for f in result.paired_fills] == [
        (D(100), D(102)),
        (D(101), D(101)),
    ]
    assert result.closed_summary.final_equity == D(402)
    assert not result.position_open_at_end


def test_n2_funding_at_the_close_instant_hits_the_held_short():
    result = run(fundings=[funding(T3, "0.0001")])
    (record,) = result.funding_records
    # -1 * 101 * 0.0001 = -0.0101 settled on the pre-fill short
    assert (record.pre_fill_perpetual_quantity, record.signed_cost) == (D(-1), D("-0.0101"))
    assert record.applied_to_open_position
    assert equities(result) == [D(400), D(401), D("402.0101"), D("402.0101")]
    final = result.final_state
    assert (final.spot.cash, final.perpetual.collateral) == (D(201), D("201.0101"))
    assert result.final_mark.portfolio_equity == D("402.0101")
    kinds = [e.kind for e in result.trace if e.time == T3]
    assert kinds == [TraceKind.FUNDING_SETTLED, TraceKind.MARK_PRE_FILL, TraceKind.PAIRED_FILL]


def test_n3_two_distinct_fundings_at_one_instant():
    result = run(fundings=[funding(T3, "0.0001", "Regular"), funding(T3, "0.00005", "Special")])
    # -0.0101 and -1 * 101 * 0.00005 = -0.00505; total -0.01515
    assert [r.signed_cost for r in result.funding_records] == [D("-0.0101"), D("-0.00505")]
    assert [r.event.funding.rate_type for r in result.funding_records] == ["Regular", "Special"]
    assert result.final_mark.portfolio_equity == D("402.01515")


def test_n4_fixed_leg_costs_without_funding():
    result = run(spot_cost=FixedCost("0.10"), perp_cost=FixedCost("0.05"))
    # open: spot 200-100-0.10 = 99.90, collateral 199.95
    # T2: 99.90+101 + 199.95 = 400.85; T3 (pre-fill): 99.90+101 + 199.95+1 = 401.85
    # close: spot 99.90+101-0.10 = 200.80, collateral 199.95+1-0.05 = 200.90 -> 401.70
    assert equities(result) == [D(400), D("400.85"), D("401.85"), D("401.70")]
    final = result.final_state
    assert (final.spot.cash, final.perpetual.collateral) == (D("200.80"), D("200.90"))
    summary = result.closed_summary
    assert summary.spot_realized_pnl + summary.perpetual_realized_pnl == D(2)  # gross
    assert summary.spot_costs + summary.perpetual_costs == D("0.30")
    assert summary.total_pnl == D("1.70")


def test_n5_costs_and_close_instant_funding():
    result = run(
        fundings=[funding(T3, "0.0001")], spot_cost=FixedCost("0.10"), perp_cost=FixedCost("0.05")
    )
    # N4 plus +0.0101 collateral from T3 onwards
    assert equities(result) == [D(400), D("400.85"), D("401.8601"), D("401.7101")]
    final = result.final_state
    assert (final.spot.cash, final.perpetual.collateral) == (D("200.80"), D("200.9101"))
    assert result.closed_summary.total_pnl == D("1.7101")


def test_n6_funding_at_the_open_instant_sees_a_flat_leg():
    result = run(fundings=[funding(T1, "0.0001")])
    (record,) = result.funding_records
    assert record.lifecycle_before is HedgeLifecycle.FLAT
    assert (record.pre_fill_perpetual_quantity, record.signed_cost) == (D(0), D(0))
    assert not record.applied_to_open_position
    assert result.final_state.applied_funding_keys == ()  # accounting API not called
    assert result.final_mark.portfolio_equity == D(402)
    kinds = [e.kind for e in result.trace if e.time == T1]
    assert kinds == [
        TraceKind.FUNDING_ZERO_RECORDED,
        TraceKind.MARK_PRE_FILL,
        TraceKind.PAIRED_FILL,
    ]


def test_n7_position_open_at_the_end_is_valued_not_closed():
    result = run(script=intents(close_at=None))
    final = result.final_state
    assert (final.spot.cash, final.spot.quantity) == (D(100), D(1))
    assert (final.perpetual.collateral, final.perpetual.quantity) == (D(200), D(-1))
    mark = result.final_mark
    # last closes 101/101: spot unrealized 101-100 = 1, perp (101-102)(-1) = 1
    assert mark.perpetual_unrealized_pnl == D(1)
    assert mark.spot_asset_value - D(100) == D(1)
    assert (final.spot.realized_pnl, final.perpetual.realized_pnl) == (D(0), D(0))
    assert (mark.portfolio_equity, mark.time) == (D(402), T4)
    assert result.position_open_at_end and result.closed_summary is None
    assert len(result.paired_fills) == 1


def test_n7_close_intent_on_the_last_candle_is_unexecuted():
    result = run(script=intents(close_at=T4))
    (unexecuted,) = result.unexecuted_intents
    assert unexecuted.intent.action is HedgeAction.CLOSE
    assert "no next candle" in unexecuted.reason
    assert result.position_open_at_end and len(result.paired_fills) == 1
    assert result.final_mark.portfolio_equity == D(402)
    assert result.trace[-1].kind is TraceKind.INTENT_UNEXECUTED


# ================================================================ S1–S12 (§19.12.5)

S_ROWS = (S_SPOT, S_PERP)


def test_s1_no_funding():
    result = run(rows=S_ROWS)
    # t0+2h: 100 + 100.5 + 200 + (101.5 - 102)(-1) = 401
    assert equities(result) == [D(400), D(401), D(402), D(402)]
    assert (result.final_state.spot.cash, result.final_state.perpetual.collateral) == (
        D(201),
        D(201),
    )


def test_s2_funding_between_candles():
    result = run(rows=S_ROWS, fundings=[funding(T2 + H / 2, "0.0001")])
    assert equities(result) == [D(400), D(401), D("402.0101"), D("402.0101")]
    assert result.final_state.perpetual.collateral == D("201.0101")
    marks_before = [p for p in result.equity_points if p.mark.time < T2 + H / 2]
    assert all(p.mark.perpetual_collateral == D(200) for p in marks_before)


def test_s3_funding_at_close_instant_uses_ms9():
    result = run(rows=S_ROWS, fundings=[funding(T3, "0.0001")])
    assert result.final_mark.portfolio_equity == D("402.0101")


def test_s4_funding_at_open_instant_is_zero():
    result = run(rows=S_ROWS, fundings=[funding(T1, "0.0001")])
    assert result.funding_records[0].signed_cost == D(0)
    assert result.final_mark.portfolio_equity == D(402)


def test_s5_two_fundings_same_instant():
    result = run(
        rows=S_ROWS,
        fundings=[
            funding(T2 + H / 2, "0.0001", "Regular"),
            funding(T2 + H / 2, "0.00005", "Special"),
        ],
    )
    assert result.final_state.perpetual.collateral == D("201.01515")
    assert result.final_mark.portfolio_equity == D("402.01515")


def test_s6_duplicate_and_conflicting_events_rejected_before_any_effect():
    with pytest.raises(ValueError, match="duplicate funding event"):
        run(rows=S_ROWS, fundings=[funding(T2, "0.0001"), funding(T2, "0.0001")])
    with pytest.raises(ValueError, match="conflicting payload"):
        run(rows=S_ROWS, fundings=[funding(T2, "0.0001"), funding(T2, "0.0002")])


def test_s7_run_boundaries():
    with pytest.raises(ValueError, match="run_start <= event_time < run_end"):
        run(rows=S_ROWS, fundings=[funding(T0 - H, "0.0001")])
    with pytest.raises(ValueError, match="run_start <= event_time < run_end"):
        run(rows=S_ROWS, fundings=[funding(T4, "0.0001")])
    result = run(rows=S_ROWS, fundings=[funding(T0, "0.0001")])  # at run start, FLAT
    assert (result.funding_records[0].signed_cost, result.final_mark.portfolio_equity) == (
        D(0),
        D(402),
    )


def test_s8_funding_after_the_close_is_a_zero_record():
    result = run(rows=S_ROWS, fundings=[funding(T3 + H / 2, "0.0001")])
    (record,) = result.funding_records
    assert record.lifecycle_before is HedgeLifecycle.FLAT_CLOSED
    assert (record.signed_cost, result.final_mark.portfolio_equity) == (D(0), D(402))


def _replay_with(spot, perp):
    return run_multileg_replay(
        pair=PAIR,
        spot=spot,
        perpetual=perp,
        funding_events=(),
        intents=intents(close_at=None),
        spot_cash=D(200),
        perpetual_collateral=D(200),
        spot_cost_model=ZERO,
        perpetual_cost_model=ZERO,
        funding_model=LinearFundingModel(),
        as_of_time=T4,
    )


def test_s9_unsynchronised_series_are_rejected():
    spot, perp = legs(S_SPOT, S_PERP)
    with pytest.raises(ValueError, match="identical open_time grids"):  # shorter spot series
        _replay_with(LegCandles(SPOT_DS, spot.candles[:3]), perp)
    gapped = LegCandles(PERP_DS, tuple(candle("BTCUSDT", k, *S_PERP[k]) for k in (0, 1, 3)))
    with pytest.raises(ValueError, match="exactly contiguous"):  # perp gap at mum 2
        _replay_with(spot, gapped)
    with pytest.raises(ValueError, match="strictly ascending"):  # unordered
        _replay_with(spot, LegCandles(PERP_DS, perp.candles[::-1]))
    four_hour = CandleDataset(
        "binance", "usdm_perpetual", "BTCUSDT", "4h", "contract_trade", BINANCE_USDM_KLINES_SOURCE
    )
    with pytest.raises(ValueError, match="do not match the dataset"):
        _replay_with(spot, LegCandles(four_hour, perp.candles))


def test_s10_decision_times_must_be_candle_closes_and_last_is_unexecuted():
    with pytest.raises(ValueError, match="not a candle availability instant"):
        run(rows=S_ROWS, script=intents(open_at=T1 + H / 2, close_at=None))
    result = run(rows=S_ROWS, script=intents(open_at=T4, close_at=None))
    assert len(result.unexecuted_intents) == 1 and result.paired_fills == ()
    assert result.final_mark.portfolio_equity == D(400)


def test_s11_deterministic_repetition():
    first = run(rows=S_ROWS, fundings=[funding(T3, "0.0001")])
    second = run(rows=S_ROWS, fundings=[funding(T3, "0.0001")])
    assert first == second


@pytest.mark.parametrize(
    ("script", "message"),
    [
        (
            (HedgeIntent(HedgeAction.OPEN, T1, D(1)), HedgeIntent(HedgeAction.OPEN, T2, D(1))),
            "one lifecycle",
        ),
        ((HedgeIntent(HedgeAction.CLOSE, T1, D(1)),), "one lifecycle"),
        (
            (HedgeIntent(HedgeAction.OPEN, T1, D(1)), HedgeIntent(HedgeAction.CLOSE, T3, D("0.5"))),
            "no partial close",
        ),
        (
            (HedgeIntent(HedgeAction.OPEN, T2, D(1)), HedgeIntent(HedgeAction.CLOSE, T2, D(1))),
            "strictly later",
        ),
    ],
)
def test_s12_lifecycle_script_rules(script, message):
    with pytest.raises(ValueError, match=message):
        run(rows=S_ROWS, script=script)
