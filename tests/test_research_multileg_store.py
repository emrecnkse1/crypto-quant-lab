"""Store-backed multi-leg replay: parity, rejection, snapshot and provenance (ST1–ST12).

Fixtures are fresh stores written in tmp_path through the real writers; the
N-fixture candle values are imported from the replay acceptance file, and every
economic expectation is a hand-derived literal.
"""

import json
import os
import socket
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext

import pytest
import test_backtest_multileg_replay as acceptance

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.multileg import HedgedPair, TradableInstrument
from crypto_quant_lab.backtest.multileg_replay import (
    HedgeAction,
    HedgeIntent,
    LegCandles,
    run_multileg_replay,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import multileg_store
from crypto_quant_lab.research import multileg_store_demo as demo
from crypto_quant_lab.research.decimal_policy import DEFAULT_DECIMAL_CONTEXT
from crypto_quant_lab.research.multileg_store import (
    CandleStoreSource,
    StoreInputError,
    default_request,
    run_store_backed_multileg_replay,
)
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import CandleDataset
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

D = Decimal
H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T4 = T0 + 4 * H
SPOT_SRC = "synthetic:test/spot"
PERP_SRC = "synthetic:test/perp"
PAIR = HedgedPair(
    "btc",
    TradableInstrument("binance", "spot", "BTCUSDT", "USDT"),
    TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT"),
)


class FixedCost:
    def __init__(self, amount) -> None:
        self.amount = D(amount)

    def calculate_cost(self, *, quantity, execution_price):
        return self.amount


def candles(rows, symbol="BTCUSDT", hours=None):
    hours = range(len(rows)) if hours is None else hours
    return [
        Candle(symbol, "1h", T0 + k * H, D(o), max(D(o), D(c)), min(D(o), D(c)), D(c), D(1))
        for k, (o, c) in zip(hours, rows, strict=True)
    ]


def write_candles(path, market, price_kind, source, rows, *, hours=None, cover=(T0, T4)):
    store = SQLiteHistoricalCandleStore(path)
    try:
        dataset = CandleDataset("binance", market, "BTCUSDT", "1h", price_kind, source)
        records = [HistoricalCandle("binance", market, c) for c in candles(rows, hours=hours)]
        store.write_ingestion_batch(
            records, dataset=dataset, covered_start=cover[0], covered_end=cover[1]
        )
    finally:
        store.close()


def funding_event(time, rate, rate_type="Regular", symbol="BTCUSDT"):
    return HistoricalFundingEvent(
        exchange="binance",
        market_type="usdm_perpetual",
        symbol=symbol,
        funding=FundingEvent(
            event_time=time, funding_rate=D(rate), reference_price=D(101), rate_type=rate_type
        ),
    )


def write_funding(path, events, *, cover=(T0, T4), symbol="BTCUSDT"):
    store = SQLiteHistoricalFundingStore(path)
    try:
        if cover is not None:
            store.write_ingestion_batch(
                events,
                exchange="binance",
                market_type="usdm_perpetual",
                symbol=symbol,
                covered_start=cover[0],
                covered_end=cover[1],
            )
    finally:
        store.close()


@pytest.fixture
def stores(tmp_path):
    spot, perp = tmp_path / "spot.db", tmp_path / "perp.db"
    write_candles(spot, "spot", "spot_trade", SPOT_SRC, acceptance.N_SPOT)
    write_candles(perp, "usdm_perpetual", "contract_trade", PERP_SRC, acceptance.N_PERP)
    write_funding(tmp_path / "f_none.db", [])
    write_funding(tmp_path / "f_t3.db", [funding_event(acceptance.T3, "0.0001")])
    write_funding(
        tmp_path / "f_t3_two.db",
        [
            funding_event(acceptance.T3, "0.0001", "Regular"),
            funding_event(acceptance.T3, "0.00005", "Special"),
        ],
    )
    return tmp_path


def intents(close=True):
    out = [HedgeIntent(HedgeAction.OPEN, T0 + H, D(1))]
    if close:
        out.append(HedgeIntent(HedgeAction.CLOSE, T0 + 3 * H, D(1)))
    return tuple(out)


def request(base, funding="f_none.db", **changes):
    fields = {
        "pair": PAIR,
        "timeframe": "1h",
        "run_start": T0,
        "run_end": T4,
        "as_of_time": T4,
        "spot": CandleStoreSource(base / "spot.db", SPOT_SRC),
        "perpetual": CandleStoreSource(base / "perp.db", PERP_SRC),
        "funding_path": base / funding,
        "intents": intents(),
        "spot_cash": D(200),
        "perpetual_collateral": D(200),
    }
    return default_request(**(fields | changes))


def files(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(directory.iterdir())}


def equities(result):
    return [p.mark.portfolio_equity for p in result.equity_points]


# ================================================================ ST1 / ST2 / ST3


@pytest.mark.parametrize(
    ("funding", "script", "spot_cost", "perp_cost"),
    [
        ("f_none.db", intents(), ZeroCostModel(), ZeroCostModel()),
        ("f_t3.db", intents(), ZeroCostModel(), ZeroCostModel()),
        ("f_t3_two.db", intents(), ZeroCostModel(), ZeroCostModel()),
        ("f_t3.db", intents(), FixedCost("0.10"), FixedCost("0.05")),
        ("f_none.db", intents(close=False), ZeroCostModel(), ZeroCostModel()),
    ],
)
def test_st1_store_path_equals_in_memory_path_on_the_same_inputs(
    stores, funding, script, spot_cost, perp_cost
):
    req = request(
        stores, funding, intents=script, spot_cost_model=spot_cost, perpetual_cost_model=perp_cost
    )
    run = run_store_backed_multileg_replay(req)
    events = {
        "f_none.db": (),
        "f_t3.db": (funding_event(acceptance.T3, "0.0001"),),
        "f_t3_two.db": (
            funding_event(acceptance.T3, "0.0001", "Regular"),
            funding_event(acceptance.T3, "0.00005", "Special"),
        ),
    }[funding]
    direct = run_multileg_replay(
        pair=PAIR,
        spot=LegCandles(run.spot.dataset, tuple(candles(acceptance.N_SPOT))),
        perpetual=LegCandles(run.perpetual.dataset, tuple(candles(acceptance.N_PERP))),
        funding_events=events,
        intents=script,
        spot_cash=D(200),
        perpetual_collateral=D(200),
        spot_cost_model=spot_cost,
        perpetual_cost_model=perp_cost,
        funding_model=LinearFundingModel(),
        as_of_time=T4,
    )
    assert run.result == direct  # fills, funding records, equity points, states, trace


def test_st2_n_fixture_values_through_the_store_path(stores):
    n1 = run_store_backed_multileg_replay(request(stores)).result
    assert equities(n1) == [D(400), D(401), D(402), D(402)]
    assert (n1.final_state.spot.cash, n1.final_state.perpetual.collateral) == (D(201), D(201))
    assert run_store_backed_multileg_replay(
        request(stores, "f_t3.db")
    ).result.final_mark.portfolio_equity == D("402.0101")
    n3 = run_store_backed_multileg_replay(request(stores, "f_t3_two.db")).result
    assert [r.signed_cost for r in n3.funding_records] == [D("-0.0101"), D("-0.00505")]
    assert n3.final_mark.portfolio_equity == D("402.01515")
    n5 = run_store_backed_multileg_replay(
        request(
            stores,
            "f_t3.db",
            spot_cost_model=FixedCost("0.10"),
            perpetual_cost_model=FixedCost("0.05"),
        )
    ).result
    assert n5.final_mark.portfolio_equity == D("401.7101")
    n7 = run_store_backed_multileg_replay(request(stores, intents=intents(close=False))).result
    final = n7.final_state
    assert (final.spot.realized_pnl, final.perpetual.realized_pnl) == (D(0), D(0))
    assert n7.final_mark.perpetual_unrealized_pnl == D(1)
    assert n7.final_mark.spot_asset_value - final.spot.quantity * final.spot.entry_price == D(1)
    assert (n7.final_mark.portfolio_equity, n7.position_open_at_end) == (D(402), True)


def test_st3_proportional_and_fixed_costs_are_distinct_configurations(stores):
    proportional = run_store_backed_multileg_replay(
        request(
            stores,
            "f_t3.db",
            spot_cost_model=ProportionalCommissionModel(rate=D("0.001")),
            perpetual_cost_model=ProportionalCommissionModel(rate=D("0.0005")),
        )
    )
    # costs 0.100 + 0.0510 + 0.101 + 0.0505 = 0.3025; 400 + 2 + 0.0101 - 0.3025
    assert proportional.result.final_mark.portfolio_equity == D("401.7076")
    fixed = run_store_backed_multileg_replay(
        request(
            stores,
            "f_t3.db",
            spot_cost_model=FixedCost("0.10"),
            perpetual_cost_model=FixedCost("0.05"),
        )
    )
    assert fixed.result.final_mark.portfolio_equity == D("401.7101")  # 400 + 2 + 0.0101 - 0.30
    assert proportional.replay_input_sha256 is not None
    assert fixed.replay_input_sha256 is None  # undescribable model: no reproducibility claim


# ================================================================ ST4 / ST5 provenance and data


def reason_of(req):
    with pytest.raises(StoreInputError) as info:
        run_store_backed_multileg_replay(req)
    return info.value.reason


def test_st4_provenance_failures_are_refused_and_never_repaired(stores):
    legacy = stores / "legacy_spot.db"
    store = SQLiteHistoricalCandleStore(legacy)
    store.write_batch([HistoricalCandle("binance", "spot", c) for c in candles(acceptance.N_SPOT)])
    store.close()
    before = files(stores)
    assert reason_of(request(stores, spot=CandleStoreSource(legacy, SPOT_SRC))) == (
        "missing_provenance"
    )
    assert reason_of(request(stores, spot=CandleStoreSource(stores / "spot.db", "binance:x"))) == (
        "provenance_mismatch"
    )
    mark = stores / "mark.db"
    write_candles(mark, "usdm_perpetual", "mark_price", PERP_SRC, acceptance.N_PERP)
    assert reason_of(request(stores, perpetual=CandleStoreSource(mark, PERP_SRC))) == (
        "provenance_mismatch"
    )
    partial = stores / "partial.db"
    write_candles(
        partial, "spot", "spot_trade", SPOT_SRC, acceptance.N_SPOT[:2], cover=(T0, T0 + 2 * H)
    )
    assert reason_of(request(stores, spot=CandleStoreSource(partial, SPOT_SRC))) == (
        "incomplete_coverage"
    )
    failed = stores / "failed.db"  # an ingestion that failed wrote nothing
    SQLiteHistoricalCandleStore(failed).close()
    assert reason_of(request(stores, spot=CandleStoreSource(failed, SPOT_SRC))) == (
        "missing_provenance"
    )
    after = files(stores)
    assert {k: after[k] for k in before} == before
    assert (
        SQLiteHistoricalCandleStore.open_read_only(legacy).query_dataset(
            "binance", "spot", "BTCUSDT", "1h"
        )
        is None
    )


def test_st5_market_data_rules(stores):
    holed = stores / "holed.db"
    write_candles(
        holed,
        "spot",
        "spot_trade",
        SPOT_SRC,
        [acceptance.N_SPOT[k] for k in (0, 1, 3)],
        hours=(0, 1, 3),
    )
    assert reason_of(request(stores, spot=CandleStoreSource(holed, SPOT_SRC))) == "missing_data"
    assert reason_of(request(stores, timeframe="4h", run_end=T0 + 4 * H)) == "missing_provenance"
    assert reason_of(request(stores, run_start=T0 + H / 2)) == "invalid_request"
    assert reason_of(request(stores, as_of_time=T0 + 3 * H)) == "invalid_request"
    plus3 = timezone(timedelta(hours=3))
    shifted = run_store_backed_multileg_replay(
        request(stores, run_start=T0.astimezone(plus3), run_end=T4.astimezone(plus3))
    )
    assert (
        shifted.result.final_mark
        == run_store_backed_multileg_replay(request(stores)).result.final_mark
    )


# ================================================================ ST6 funding


def test_st6_funding_coverage_empty_versus_uncollected(stores):
    empty = run_store_backed_multileg_replay(request(stores, "f_none.db"))
    assert (empty.funding.event_count, empty.funding.quality_status) == (0, "PASS")
    assert empty.funding.source_recorded is False
    write_funding(stores / "never.db", [], cover=None)  # schema only: nothing collected
    assert reason_of(request(stores, "never.db")) == "incomplete_coverage"
    write_funding(stores / "half.db", [], cover=(T0, T0 + 2 * H))
    assert reason_of(request(stores, "half.db")) == "incomplete_coverage"
    write_funding(stores / "eth.db", [], symbol="ETHUSDT")  # another pair's coverage only
    assert reason_of(request(stores, "eth.db")) == "incomplete_coverage"


def test_st6_flat_and_closed_zero_records_come_through_the_store(stores):
    write_funding(
        stores / "edges.db",
        [funding_event(T0, "0.0001"), funding_event(acceptance.T3 + H / 2, "0.0001")],
    )
    run = run_store_backed_multileg_replay(request(stores, "edges.db"))
    assert [(r.lifecycle_before.value, r.signed_cost) for r in run.result.funding_records] == [
        ("FLAT", D(0)),
        ("FLAT_CLOSED", D(0)),
    ]
    assert run.result.final_mark.portfolio_equity == D(402)


# ================================================================ ST7 / ST8 read-only and snapshot


def test_st7_missing_paths_are_not_created_and_sources_stay_unchanged(stores):
    before = files(stores)
    with pytest.raises(FileNotFoundError):
        run_store_backed_multileg_replay(request(stores, "missing.db"))
    assert not (stores / "missing.db").exists()
    run_store_backed_multileg_replay(request(stores, "f_t3.db"))
    reason_of(request(stores, spot=CandleStoreSource(stores / "spot.db", "binance:x")))
    assert files(stores) == before  # bytes, mtimes, and no -wal/-shm/-journal files


def test_st8_rows_and_provenance_come_from_one_snapshot_per_store(stores, monkeypatch):
    perp = stores / "perp.db"
    switch = sqlite3.connect(perp)
    assert switch.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    switch.close()
    writer = sqlite3.connect(perp)
    original = SQLiteHistoricalCandleStore.query_coverage
    injected = []

    def query_coverage_then_concurrent_commit(self, *args):
        result = original(self, *args)
        if args[1] == "usdm_perpetual" and not injected:
            writer.execute(
                "UPDATE historical_candles SET close = '999', high = '999' "
                "WHERE market_type = 'usdm_perpetual'"
            )
            writer.commit()
            injected.append(True)
        return result

    monkeypatch.setattr(
        SQLiteHistoricalCandleStore, "query_coverage", query_coverage_then_concurrent_commit
    )
    run = run_store_backed_multileg_replay(request(stores))
    assert injected and run.result.final_mark.portfolio_equity == D(402)  # snapshot values
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "query_coverage", original)
    after = SQLiteHistoricalCandleStore.open_read_only(perp)
    assert after.query("binance", "usdm_perpetual", "BTCUSDT", "1h", T0, T4)[0].candle.close == (
        D(999)
    )
    after.close()
    writer.close()
    assert "no atomic snapshot across stores" in run.snapshot_scope


def test_st8_connections_are_released_on_failure(stores):
    reason_of(request(stores, perpetual=CandleStoreSource(stores / "perp.db", "binance:x")))
    for name in ("spot.db", "perp.db", "f_none.db"):
        os.replace(stores / name, stores / f"moved-{name}")  # fails on Windows if still open


def test_each_store_is_queried_once(stores, monkeypatch):
    calls = []
    original = SQLiteHistoricalCandleStore.query

    def counting(self, *args):
        calls.append(args[1])
        return original(self, *args)

    monkeypatch.setattr(SQLiteHistoricalCandleStore, "query", counting)
    run_store_backed_multileg_replay(request(stores))
    assert calls == ["spot", "usdm_perpetual"]


# ================================================================ ST9 fingerprints


def test_st9_fingerprints_track_consumed_inputs_only(stores):
    base = run_store_backed_multileg_replay(request(stores, "f_t3.db"))
    same = run_store_backed_multileg_replay(request(stores, "f_t3.db"))
    assert base.replay_input_sha256 == same.replay_input_sha256
    for changes in (
        {"intents": intents(close=False)},
        {"spot_cost_model": ProportionalCommissionModel(rate=D("0.001"))},
        {"spot_cash": D(300)},
        {"decimal_context": {**DEFAULT_DECIMAL_CONTEXT, "prec": 50}},
        {"funding_path": stores / "f_none.db"},
    ):
        assert (
            run_store_backed_multileg_replay(
                request(stores, "f_t3.db", **changes)
            ).replay_input_sha256
            != base.replay_input_sha256
        )
    # a row outside the queried window changes store-level provenance, not the replay input
    extra = SQLiteHistoricalCandleStore(stores / "spot.db")
    extra.write_ingestion_batch(
        [HistoricalCandle("binance", "spot", candles([("101", "150")], hours=[4])[0])],
        dataset=CandleDataset("binance", "spot", "BTCUSDT", "1h", "spot_trade", SPOT_SRC),
        covered_start=T4,
        covered_end=T4 + H,
    )
    extra.close()
    later = run_store_backed_multileg_replay(request(stores, "f_t3.db"))
    assert later.replay_input_sha256 == base.replay_input_sha256
    assert later.spot.consumed_sha256 == base.spot.consumed_sha256


def test_st9_price_changes_change_the_fingerprint(tmp_path, stores):
    other = tmp_path / "other"
    other.mkdir()
    spot_rows = list(acceptance.N_SPOT)
    spot_rows[2] = ("101", "100.5")
    write_candles(other / "spot.db", "spot", "spot_trade", SPOT_SRC, spot_rows)
    write_candles(
        other / "perp.db", "usdm_perpetual", "contract_trade", PERP_SRC, acceptance.N_PERP
    )
    write_funding(other / "f_none.db", [])
    assert run_store_backed_multileg_replay(request(other)).replay_input_sha256 != (
        run_store_backed_multileg_replay(request(stores)).replay_input_sha256
    )


# ================================================================ ST10 / ST11


def test_st10_explicit_context_and_ambient_isolation(stores, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    reference = run_store_backed_multileg_replay(
        request(stores, "f_t3.db", spot_cost_model=ProportionalCommissionModel(rate=D("0.001")))
    )
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        again = run_store_backed_multileg_replay(
            request(stores, "f_t3.db", spot_cost_model=ProportionalCommissionModel(rate=D("0.001")))
        )
        with pytest.raises(StoreInputError):
            run_store_backed_multileg_replay(request(stores, as_of_time=T0))
        assert (getcontext().prec, getcontext().rounding) == (3, ROUND_DOWN)
    assert again.result == reference.result
    assert again.replay_input_sha256 == reference.replay_input_sha256


def test_st11_replay_is_never_called_for_invalid_inputs(stores, monkeypatch):
    calls = []
    real = multileg_store.run_multileg_replay

    def spy(**kwargs):
        calls.append(True)
        return real(**kwargs)

    monkeypatch.setattr(multileg_store, "run_multileg_replay", spy)
    for bad in (
        request(stores, spot=CandleStoreSource(stores / "spot.db", "binance:x")),
        request(stores, run_end=T0),
        request(stores, "missing.db"),
    ):
        with pytest.raises((StoreInputError, FileNotFoundError)):
            run_store_backed_multileg_replay(bad)
    assert calls == []
    run_store_backed_multileg_replay(request(stores))
    assert calls == [True]


# ================================================================ demo (E)


def test_demo_runs_end_to_end_and_repeats_identically(tmp_path, capsys):
    assert demo.main(["--output", str(tmp_path / "a")]) == 0
    assert demo.main(["--output", str(tmp_path / "b")]) == 0
    a = json.loads((tmp_path / "a" / "report.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "b" / "report.json").read_text(encoding="utf-8"))
    assert a["status"] == "succeeded"
    assert a["deterministic_sha256"] == b["deterministic_sha256"]
    results = a["deterministic"]["results"]
    assert D(results["proportional_costs_and_funding"]["final_equity"]) == D("401.7076")
    assert results["open_at_end"]["position_open_at_end"] is True
    assert results["closed_no_funding"]["evidence"]["spot"]["dataset"][5] == demo.SPOT_SOURCE
    assert results["close_instant_funding"]["evidence"]["funding"]["source_recorded"] is False
    assert str(tmp_path) not in json.dumps(a["deterministic"])
    before = (tmp_path / "a" / "report.json").read_bytes()
    assert demo.main(["--output", str(tmp_path / "a")]) == 2
    assert (tmp_path / "a" / "report.json").read_bytes() == before


def test_demo_with_an_unverifiable_source_produces_a_failed_bundle(tmp_path, monkeypatch):
    real = demo.scenario_request

    def wrong_source(scenario, paths):
        req = real(scenario, paths)
        return replace(req, spot=CandleStoreSource(req.spot.path, "binance:unverified"))

    monkeypatch.setattr(demo, "scenario_request", wrong_source)
    _path, report = demo.run_store_demo(tmp_path / "f")
    assert report["status"] == "failed"
    assert all("[provenance_mismatch]" in e for e in report["deterministic"]["errors"])
    assert report["deterministic"]["results"] == {}
