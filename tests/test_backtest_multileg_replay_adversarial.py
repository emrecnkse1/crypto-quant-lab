"""Adversarial, causality, metamorphic and independent-oracle checks for the multi-leg replay.

The oracle below is a small test-only ledger written from the accounting rules
in FUNDING_RESEARCH_SPEC.md Bölüm 19.10.1 and the MS9 order; it never calls
the production accounting or replay functions to obtain expected values.
Randomised scenarios use `random.Random(SEED)` integers turned into Decimals
(never floats): SEED = 20260925, SCENARIOS = 40.
"""

import inspect
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.multileg import HedgedPair, TradableInstrument
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
from crypto_quant_lab.research.decimal_policy import DEFAULT_DECIMAL_CONTEXT, build_context
from crypto_quant_lab.storage.datasets import (
    BINANCE_USDM_KLINES_SOURCE,
    CandleDataset,
    binance_usdm_index_price_dataset,
)

D = Decimal
H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
SPOT = TradableInstrument("binance", "spot", "BTCUSDT", "USDT")
PERP = TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT")
PAIR = HedgedPair("btc", SPOT, PERP)
SPOT_DS = CandleDataset("binance", "spot", "BTCUSDT", "1h", "spot_trade", "binance:spot-klines")
PERP_DS = CandleDataset(
    "binance", "usdm_perpetual", "BTCUSDT", "1h", "contract_trade", BINANCE_USDM_KLINES_SOURCE
)
SEED = 20260925
SCENARIOS = 40


class FixedCost:
    def __init__(self, amount) -> None:
        self.amount = D(amount)

    def calculate_cost(self, *, quantity, execution_price):
        return self.amount


def at(k):
    return T0 + k * H


def series(rows, dataset=SPOT_DS, symbol="BTCUSDT"):
    candles = []
    for k, (open_, close, *extra) in enumerate(rows):
        o, c = D(open_), D(close)
        high = D(extra[0]) if extra else max(o, c)
        low = D(extra[1]) if extra else min(o, c)
        candles.append(Candle(symbol, "1h", at(k), o, high, low, c, D(1)))
    return LegCandles(dataset, tuple(candles))


BASE_SPOT = [("100", "100"), ("100", "101"), ("101", "101"), ("101", "101")]
BASE_PERP = [("102", "102"), ("102", "102"), ("102", "101"), ("101", "101")]


def funding(time, rate, rate_type="Regular", ref="101", symbol="BTCUSDT"):
    return HistoricalFundingEvent(
        exchange="binance",
        market_type="usdm_perpetual",
        symbol=symbol,
        funding=FundingEvent(
            event_time=time, funding_rate=D(rate), reference_price=D(ref), rate_type=rate_type
        ),
    )


def script(open_k=1, close_k=3, qty="1"):
    out = [HedgeIntent(HedgeAction.OPEN, at(open_k), D(qty))]
    if close_k is not None:
        out.append(HedgeIntent(HedgeAction.CLOSE, at(close_k), D(qty)))
    return tuple(out)


def replay(
    spot_rows=BASE_SPOT,
    perp_rows=BASE_PERP,
    *,
    fundings=(),
    intents=None,
    spot_cost=None,
    perp_cost=None,
    spot_cash="200",
    as_of=None,
    spot=None,
    perp=None,
):
    spot = spot or series(spot_rows, SPOT_DS)
    perp = perp or series(perp_rows, PERP_DS)
    n = len(spot.candles)
    return run_multileg_replay(
        pair=PAIR,
        spot=spot,
        perpetual=perp,
        funding_events=tuple(fundings),
        intents=script() if intents is None else intents,
        spot_cash=D(spot_cash),
        perpetual_collateral=D(200),
        spot_cost_model=spot_cost or ZeroCostModel(),
        perpetual_cost_model=perp_cost or ZeroCostModel(),
        funding_model=LinearFundingModel(),
        as_of_time=as_of or at(n),
    )


# ================================================================ independent oracle


def oracle(spot_rows, perp_rows, open_k, close_k, qty, fundings, spot_fee, perp_fee, *, bug=None):
    """Test-only ledger: returns (pre-fill equities, final equity). `bug` injects a known defect."""
    rows = list(zip(spot_rows, perp_rows, strict=True))
    n = len(rows)
    spot_cash, spot_qty, coll, perp_qty, perp_entry = D(200), D(0), D(200), D(0), None
    pending = sorted(fundings, key=lambda f: (f[0], f[3]))
    equities = []

    def value(spot_close, perp_close):
        unreal = D(0) if perp_entry is None else (perp_close - perp_entry) * perp_qty
        notional = -perp_qty * perp_close if bug == "count_notional" else D(0)
        return spot_cash + spot_qty * spot_close + coll + unreal + notional

    for i, ((_so, sc), (_po, pc)) in enumerate(rows):
        t = at(i + 1)
        while pending and pending[0][0] <= t:
            _time, rate, ref, _rt = pending.pop(0)
            cost = perp_qty * ref * rate
            coll -= -cost if bug == "funding_sign" else cost
        equities.append(value(D(sc), D(pc)))
        if i + 1 < n and i == open_k - 1:
            so, po = D(rows[i + 1][0][0]), D(rows[i + 1][1][0])
            spot_cash -= qty * so + spot_fee
            spot_qty, perp_qty, perp_entry = qty, -qty, po
            coll -= perp_fee * (2 if bug == "double_fee" else 1)
        if close_k is not None and i + 1 < n and i == close_k - 1:
            so, po = D(rows[i + 1][0][0]), D(rows[i + 1][1][0])
            spot_cash += qty * so - spot_fee
            coll += qty * (perp_entry - po) - perp_fee
            spot_qty, perp_qty, perp_entry = D(0), D(0), None
    last_spot, last_perp = D(rows[-1][0][1]), D(rows[-1][1][1])
    return equities, value(last_spot, last_perp)


def random_scenario(rng):
    n = rng.randint(4, 9)
    spot_rows, perp_rows = [], []
    for _ in range(n):
        spot_rows.append(
            (str(D(rng.randint(9000, 11000)) / 100), str(D(rng.randint(9000, 11000)) / 100))
        )
        perp_rows.append(
            (str(D(rng.randint(9000, 11000)) / 100), str(D(rng.randint(9000, 11000)) / 100))
        )
    open_k = rng.randint(1, n - 2)
    close_k = rng.choice([None, rng.randint(open_k + 1, n)])
    times = sorted(
        {T0 + timedelta(minutes=30 * rng.randint(0, 2 * n - 1)) for _ in range(rng.randint(0, 4))}
    )
    fundings = [
        (t, D(rng.randint(-30, 30)) / D(100000), D(rng.randint(9000, 11000)) / 100, "Regular")
        for t in times
    ]
    fees = (D(rng.randint(0, 20)) / 100, D(rng.randint(0, 20)) / 100)
    return spot_rows, perp_rows, open_k, close_k, D(rng.randint(1, 15)) / 10, fundings, fees


def test_randomised_scenarios_match_the_independent_oracle():
    rng = random.Random(SEED)
    checked_equity_points = 0
    for _ in range(SCENARIOS):
        spot_rows, perp_rows, open_k, close_k, qty, fundings, (sf, pf) = random_scenario(rng)
        result = replay(
            spot_rows,
            perp_rows,
            fundings=[funding(t, str(r), rt, str(ref)) for t, r, ref, rt in fundings],
            intents=script(open_k, close_k, str(qty)),
            spot_cost=FixedCost(sf),
            perp_cost=FixedCost(pf),
            spot_cash="2000",
        )
        equities, final = oracle(spot_rows, perp_rows, open_k, close_k, qty, fundings, sf, pf)
        equities = [e + 1800 for e in equities]  # the replay started with 2000 spot cash
        assert [p.mark.portfolio_equity for p in result.equity_points] == equities
        assert result.final_mark.portfolio_equity == final + 1800
        checked_equity_points += len(equities)
    assert checked_equity_points > 150


@pytest.mark.parametrize("bug", ["count_notional", "funding_sign", "double_fee"])
def test_the_oracle_detects_known_defects(bug):
    fundings = [(at(2) + H / 2, D("0.0001"), D(101), "Regular")]
    good_equities, good_final = oracle(
        BASE_SPOT, BASE_PERP, 1, None, D(1), fundings, D("0.1"), D("0.05")
    )
    bad_equities, bad_final = oracle(
        BASE_SPOT, BASE_PERP, 1, None, D(1), fundings, D("0.1"), D("0.05"), bug=bug
    )
    assert (bad_equities, bad_final) != (good_equities, good_final)
    result = replay(
        fundings=[funding(at(2) + H / 2, "0.0001")],
        intents=script(1, None),
        spot_cost=FixedCost("0.1"),
        perp_cost=FixedCost("0.05"),
    )
    assert [p.mark.portfolio_equity for p in result.equity_points] == good_equities
    assert result.final_mark.portfolio_equity == good_final


# ================================================================ D1 time and funding


def test_reverse_rate_type_order_and_foreign_events_are_rejected():
    with pytest.raises(ValueError, match=r"not ordered \(event_time, rate_type\)"):
        replay(fundings=[funding(at(3), "0.0001", "Special"), funding(at(3), "0.0001", "Regular")])
    with pytest.raises(ValueError, match="not for the pair's perpetual"):
        replay(fundings=[funding(at(2), "0.0001", symbol="ETHUSDT")])


def test_zero_positive_negative_funding_signs():
    for rate, expected in (("0", "402"), ("0.0001", "402.0101"), ("-0.0001", "401.9899")):
        result = replay(fundings=[funding(at(3), rate)])
        # short -1 at T3, reference 101: cost = -1 * 101 * rate
        assert result.final_mark.portfolio_equity == D(expected)


def test_as_of_before_the_data_and_naive_or_equivalent_times():
    with pytest.raises(ValueError, match="not yet available"):
        replay(as_of=at(3))
    with pytest.raises(ValueError, match="timezone-aware"):
        HedgeIntent(HedgeAction.OPEN, datetime(2026, 1, 1, 1), D(1))  # noqa: DTZ001
    plus3 = timezone(timedelta(hours=3))
    shifted = tuple(replace(i, decision_time=i.decision_time.astimezone(plus3)) for i in script())
    assert replay(intents=shifted).final_mark == replay().final_mark


def test_funding_is_never_shifted_by_a_publication_lag():
    result = replay(fundings=[funding(at(2) + H / 2, "0.0001")])
    (settled,) = [e for e in result.trace if e.kind is TraceKind.FUNDING_SETTLED]
    assert settled.time == at(2) + H / 2  # the settlement instant itself
    marks = [p.mark for p in result.equity_points]
    assert marks[1].perpetual_collateral == D(200)  # T2 mark: not yet settled
    assert marks[2].perpetual_collateral == D("200.0101")  # T3 mark: settled


# ================================================================ D2 market inputs


def test_wrong_dataset_identities_are_rejected():
    index = binance_usdm_index_price_dataset("BTCUSDT", "1h")
    mark = CandleDataset("binance", "usdm_perpetual", "BTCUSDT", "1h", "mark_price", "x")
    eth = CandleDataset("binance", "spot", "ETHUSDT", "1h", "spot_trade", "x")
    for spot, perp, message in (
        (None, series(BASE_PERP, index), "not a tradable leg"),
        (None, series(BASE_PERP, mark), "not a tradable leg"),
        (series(BASE_SPOT, PERP_DS), None, "not the pair's spot"),
        (series(BASE_SPOT, eth), None, "not the pair's spot"),
        (series(BASE_SPOT, SPOT_DS, symbol="ETHUSDT"), None, "do not match the dataset"),
    ):
        with pytest.raises(ValueError, match=message):
            replay(spot=spot, perp=perp)


def test_empty_and_invalid_price_series_are_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        replay(spot=LegCandles(SPOT_DS, ()), perp=LegCandles(PERP_DS, ()))
    with pytest.raises(ValueError, match="must be finite"):
        Candle("BTCUSDT", "1h", T0, D("NaN"), D(1), D(1), D(1), D(1))
    with pytest.raises(ValueError, match="high cannot be less"):
        series([("100", "100", "99", "98")])


# ================================================================ D3 lifecycle and atomicity


def test_insufficient_cash_and_invalid_costs_fail_without_a_result():
    with pytest.raises(ValueError, match="insufficient spot cash"):
        replay(spot_cash="99.99")

    class Bad:
        def calculate_cost(self, *, quantity, execution_price):
            return D(-1)

    with pytest.raises(ValueError, match="invalid cost"):
        replay(perp_cost=Bad())


def test_no_warmup_or_evaluation_start_is_offered():
    params = inspect.signature(run_multileg_replay).parameters
    assert not {"warmup", "evaluation_start", "context_start"} & set(params)
    assert replay().warmup_supported is False


def test_costs_are_booked_once_per_leg_ledger():
    result = replay(spot_cost=FixedCost("0.10"), perp_cost=FixedCost("0.05"))
    final = result.final_state
    assert (final.spot.costs_paid, final.perpetual.costs_paid) == (D("0.20"), D("0.10"))
    assert [(f.spot.cost, f.perpetual.cost) for f in result.paired_fills] == [
        (D("0.10"), D("0.05")),
        (D("0.10"), D("0.05")),
    ]


# ================================================================ D4 causality


def test_later_candle_high_low_close_never_change_the_fill_or_earlier_marks():
    changed_spot = list(BASE_SPOT)
    changed_spot[1] = ("100", "95", "130", "90")  # fill candle: OPEN kept, HIGH/LOW/CLOSE changed
    base, changed = replay(), replay(changed_spot)
    assert base.paired_fills[0] == changed.paired_fills[0]
    assert base.equity_points[0] == changed.equity_points[0]  # T1 mark precedes candle 1
    assert base.paired_fills[0].spot.execution.price == D(100)  # candle 1 OPEN, not candle 0 OPEN
    assert base.paired_fills[0].time == at(1)


def test_decision_close_is_not_the_fill_price():
    rows = list(BASE_SPOT)
    rows[0] = ("100", "150", "150", "100")  # decision candle closes at 150
    result = replay(rows)
    assert result.paired_fills[0].spot.execution.price == D(100)  # next OPEN, not 150


def test_suffix_does_not_rewrite_the_common_prefix():
    short = replay(intents=script(1, None))
    longer = replay(
        BASE_SPOT + [("101", "120"), ("120", "80")],
        BASE_PERP + [("101", "119"), ("119", "81")],
        intents=script(1, None),
    )
    assert longer.equity_points[: len(short.equity_points)] == short.equity_points
    assert longer.paired_fills == short.paired_fills
    assert longer.trace[: len(short.trace)] == short.trace


# ================================================================ D5 metamorphic


def test_parallel_price_shift_leaves_hedged_pnl_unchanged():
    def shift(rows, k):
        return [(str(D(o) + k), str(D(c) + k)) for o, c in rows]

    base = replay()
    shifted = replay(shift(BASE_SPOT, 7), shift(BASE_PERP, 7))
    assert shifted.final_mark.total_pnl == base.final_mark.total_pnl == D(2)


def test_costs_and_funding_move_equity_by_exactly_their_amounts():
    base = replay().final_mark.portfolio_equity
    costly = replay(spot_cost=FixedCost("0.3"), perp_cost=FixedCost("0.2")).final_mark
    assert costly.portfolio_equity == base - D("1.0")  # 2 * 0.3 + 2 * 0.2
    funded = replay(fundings=[funding(at(2), "0.0002", ref="100")]).final_mark
    assert funded.portfolio_equity == base + D("0.02")  # -(-1 * 100 * 0.0002)


def test_prices_after_the_close_do_not_move_a_closed_result():
    rows_spot = BASE_SPOT[:3] + [("101", "250")]
    rows_perp = BASE_PERP[:3] + [("101", "3")]
    assert replay(rows_spot, rows_perp).final_mark.portfolio_equity == D(402)


# ================================================================ D6 Decimal context


def test_explicit_context_gives_identical_results_under_a_hostile_ambient_context():
    with localcontext(build_context(DEFAULT_DECIMAL_CONTEXT)):
        reference = replay(
            fundings=[funding(at(3), "0.000123", ref="101.987654")],
            spot_cost=FixedCost("0.123456789"),
            perp_cost=FixedCost("0.0987654321"),
        )
    with localcontext() as ambient:
        ambient.prec = 4
        ambient.rounding = ROUND_DOWN
        with localcontext(build_context(DEFAULT_DECIMAL_CONTEXT)):
            again = replay(
                fundings=[funding(at(3), "0.000123", ref="101.987654")],
                spot_cost=FixedCost("0.123456789"),
                perp_cost=FixedCost("0.0987654321"),
            )
            with pytest.raises(ValueError, match="insufficient spot cash"):
                replay(spot_cash="1")
        assert (getcontext().prec, getcontext().rounding) == (4, ROUND_DOWN)
    assert again == reference


def test_duplicates_are_caught_even_while_flat_and_every_event_is_recorded():
    with pytest.raises(ValueError, match="duplicate funding event"):
        replay(fundings=[funding(T0, "0.0001"), funding(T0, "0.0001")])  # both before the open
    events = [funding(T0, "0.0001"), funding(at(2), "0.0001"), funding(at(3) + H / 2, "0.0001")]
    result = replay(fundings=events)
    # FLAT zero record, settled on the open short, FLAT_CLOSED zero record
    assert [r.lifecycle_before.value for r in result.funding_records] == [
        "FLAT",
        "HEDGED_OPEN",
        "FLAT_CLOSED",
    ]
    assert [r.signed_cost for r in result.funding_records] == [D(0), D("-0.0101"), D(0)]
    assert result.final_state.applied_funding_keys == (events[1].canonical_key,)
