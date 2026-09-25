"""Config-driven multi-leg research run (FUNDING_RESEARCH_SPEC.md Bölüm 19.15, C1–C12).

Stores are fresh files written in tmp_path through the real writers; candle
values come from the replay acceptance file (N fixture); every economic
expectation is a hand-derived literal (comments show the arithmetic) — none
is computed by the code under test.
"""

import decimal
import importlib
import json
import os
import shutil
import socket
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

import pytest
import test_backtest_multileg_replay as acceptance

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.multileg import HedgedPair, TradableInstrument
from crypto_quant_lab.backtest.multileg_replay import HedgeAction, HedgeIntent
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import cli, multileg_config, multileg_store
from crypto_quant_lab.research.decimal_policy import normalize_decimal_context
from crypto_quant_lab.research.multileg_config import load_multileg_config
from crypto_quant_lab.research.multileg_offline import result_view
from crypto_quant_lab.research.multileg_store import (
    CandleStoreSource,
    StoreBackedMultiLegRequest,
    run_store_backed_multileg_replay,
)
from crypto_quant_lab.research.report import canonical_json
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import CandleDataset
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

D = Decimal
H = timedelta(hours=1)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T4 = T0 + 4 * H
SPOT_SRC = "synthetic:test-config/spot"
PERP_SRC = "synthetic:test-config/perp"
PAIR = HedgedPair(
    "btc",
    TradableInstrument("binance", "spot", "BTCUSDT", "USDT"),
    TradableInstrument("binance", "usdm_perpetual", "BTCUSDT", "USDT"),
)
EXTRA_RESULT_KEYS = {
    "initial_wallets",
    "intent_count",
    "paired_fill_count",
    "no_trade_explanation",
    "identity_scope",
    "resolved_config",
    "input_evidence",
    "multileg_input_sha256",
}


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- store writers (real writers)


def write_candles(path, market, price_kind, source, rows, *, hours=None, cover=(T0, T4)):
    hours = range(len(rows)) if hours is None else hours
    store = SQLiteHistoricalCandleStore(path)
    try:
        records = [
            HistoricalCandle("binance", market, Candle("BTCUSDT", "1h", T0 + k * H, D(o),
                             max(D(o), D(c)), min(D(o), D(c)), D(c), D(1)))
            for k, (o, c) in zip(hours, rows, strict=True)
        ]  # fmt: skip
        store.write_ingestion_batch(
            records,
            dataset=CandleDataset("binance", market, "BTCUSDT", "1h", price_kind, source),
            covered_start=cover[0],
            covered_end=cover[1],
        )
    finally:
        store.close()


def event(time, rate, rate_type="Regular", symbol="BTCUSDT"):
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


def build_fixture(directory, *, spot_rows=acceptance.N_SPOT, spot_src=SPOT_SRC, perp_src=PERP_SRC):
    directory.mkdir()
    write_candles(directory / "spot.db", "spot", "spot_trade", spot_src, spot_rows)
    write_candles(
        directory / "perp.db", "usdm_perpetual", "contract_trade", perp_src, acceptance.N_PERP
    )
    write_funding(directory / "f_none.db", [])
    write_funding(directory / "f_t3.db", [event(acceptance.T3, "0.0001")])
    write_funding(
        directory / "f_t3_two.db",
        [event(acceptance.T3, "0.0001", "Regular"), event(acceptance.T3, "0.00005", "Special")],
    )
    return directory


@pytest.fixture
def fx(tmp_path):
    return build_fixture(tmp_path / "fx")


# ---------------------------------------------------------------- configs


def intent(action, hour, quantity="1"):
    return {"action": action, "decision_time": iso(T0 + hour * H), "quantity": quantity}


OPEN_CLOSE = [intent("OPEN", 1), intent("CLOSE", 3)]
ZERO = {"model": "zero"}


def base_config(**changes):
    config = {
        "config_kind": "multileg_replay",
        "config_version": 1,
        "pair": {
            "pair_id": "btc",
            "exchange": "binance",
            "spot_symbol": "BTCUSDT",
            "perpetual_symbol": "BTCUSDT",
            "quote_asset": "USDT",
        },
        "timeframe": "1h",
        "run_start": iso(T0),
        "run_end": iso(T4),
        "as_of": iso(T4),
        "stores": {"spot": "spot.db", "perpetual": "perp.db", "funding": "f_none.db"},
        "sources": {"spot": SPOT_SRC, "perpetual": PERP_SRC},
        "wallets": {"spot_cash": "200", "perpetual_collateral": "200"},
        "costs": {"spot": ZERO, "perpetual": ZERO},
        "funding_model": {"model": "linear"},
        "intents": OPEN_CLOSE,
    }
    return config | changes


def with_funding(name, **changes):
    config = base_config(**changes)
    config["stores"] = {**config["stores"], "funding": name}
    return config


PROPORTIONAL = {
    "spot": {"model": "proportional_commission", "rate": "0.001"},
    "perpetual": {"model": "proportional_commission", "rate": "0.0005"},
}


def write_config(directory, config, name="config.json"):
    path = directory / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def run_cli(config_path, output):
    code = cli.main(["multileg-replay", "--config", str(config_path), "--output", str(output)])
    return code, json.loads((output / "report.json").read_text(encoding="utf-8"))


def run_config(directory, config, output, name="config.json"):
    return run_cli(write_config(directory, config, name), output)


def equities(report):
    return [p["portfolio_equity"] for p in report["deterministic"]["results"]["equity_timeline_pre_fill"]]  # fmt: skip


def checks(report):
    return {c["name"]: c["status"] for c in report["deterministic"]["checks"]}


def inputs(report):
    return {i["role"]: i for i in report["deterministic"]["inputs"]}


def files(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(directory.iterdir())}


# ================================================================ C1 end to end through the store runner


def test_c1_example_runs_config_to_read_only_runner_to_replay_to_report(tmp_path, monkeypatch):
    calls = {"runner": 0, "candle_open": [], "funding_open": 0, "replay": 0}
    real_runner = multileg_store.run_store_backed_multileg_replay
    real_candle_open = SQLiteHistoricalCandleStore.open_read_only.__func__
    real_funding_open = SQLiteHistoricalFundingStore.open_read_only.__func__
    real_replay = multileg_store.run_multileg_replay

    def runner(request):
        calls["runner"] += 1
        return real_runner(request)

    def candle_open(cls, path):
        calls["candle_open"].append(Path(path).name)
        return real_candle_open(cls, path)

    def funding_open(cls, path):
        calls["funding_open"] += 1
        return real_funding_open(cls, path)

    def replay(**kwargs):
        calls["replay"] += 1
        return real_replay(**kwargs)

    assert cli.main(["multileg-example", "--output", str(tmp_path / "ex")]) == 0
    monkeypatch.setattr(multileg_store, "run_store_backed_multileg_replay", runner)
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "open_read_only", classmethod(candle_open))
    monkeypatch.setattr(SQLiteHistoricalFundingStore, "open_read_only", classmethod(funding_open))
    monkeypatch.setattr(multileg_store, "run_multileg_replay", replay)
    code, report = run_cli(tmp_path / "ex" / "config.json", tmp_path / "out")
    assert code == 0 and report["status"] == "succeeded" and report["warning_count"] == 0
    assert calls == {"runner": 1, "candle_open": ["spot.db", "perpetual.db"], "funding_open": 1,
                     "replay": 1}  # fmt: skip
    # proportional 0.001 / 0.0005, funding 0.0001 at T3 on the held short (§19.12 demo):
    # 400; 399.9 + 101 + 199.949 + 0 = 400.849; +1 perp PnL + 0.0101 funding = 401.8591;
    # close costs 0.101 + 0.0505 -> 401.7076
    assert equities(report) == ["400", "400.8490", "401.8591", "401.7076"]
    assert report["deterministic"]["results"]["final_equity"] == "401.7076"


def test_c1_a_controlled_config_change_changes_the_result(tmp_path):
    assert cli.main(["multileg-example", "--output", str(tmp_path / "ex")]) == 0
    config = json.loads((tmp_path / "ex" / "config.json").read_text(encoding="utf-8"))
    open_end = config | {"intents": config["intents"][:1]}
    code, report = run_config(tmp_path / "ex", open_end, tmp_path / "o1", "open.json")
    # no close: 401.8591 stays (spot 99.9 + 101, perp 199.949 + 1 + 0.0101), no closing cost
    assert (code, report["deterministic"]["results"]["final_equity"]) == (0, "401.8591")
    assert report["deterministic"]["results"]["position_open_at_end"] is True
    assert checks(report)["position.at_end"] == "warning"
    free_perp = config | {"costs": {**config["costs"], "perpetual": {"model": "zero"}}}
    code, report = run_config(tmp_path / "ex", free_perp, tmp_path / "o2", "free.json")
    # N2 402.0101 minus spot costs 0.1 + 0.101 = 401.8091
    assert (code, report["deterministic"]["results"]["final_equity"]) == (0, "401.8091")


def test_c1_results_follow_the_store_contents(tmp_path):
    rows = list(acceptance.N_SPOT)
    rows[3] = ("101", "103")
    fixture = build_fixture(tmp_path / "moved-price", spot_rows=rows)
    code, report = run_config(fixture, base_config(intents=[intent("OPEN", 1)]), tmp_path / "o")
    # open at end: spot cash 100 + asset 103, perp 200 + (102 - 101) -> 404
    assert (code, report["deterministic"]["results"]["final_equity"]) == (0, "404")


# ================================================================ C2 parity with the programmatic runner


def programmatic(base, funding, script, spot_cost, perp_cost):
    return StoreBackedMultiLegRequest(
        pair=PAIR,
        timeframe="1h",
        run_start=T0,
        run_end=T4,
        as_of_time=T4,
        spot=CandleStoreSource(base / "spot.db", SPOT_SRC),
        perpetual=CandleStoreSource(base / "perp.db", PERP_SRC),
        funding_path=base / funding,
        intents=script,
        spot_cash=D(200),
        perpetual_collateral=D(200),
        spot_cost_model=spot_cost,
        perpetual_cost_model=perp_cost,
        funding_model=LinearFundingModel(),
        decimal_context=normalize_decimal_context(None),
    )


OPEN_T1 = HedgeIntent(HedgeAction.OPEN, T0 + H, D(1))
CLOSE_T3 = HedgeIntent(HedgeAction.CLOSE, T0 + 3 * H, D(1))


@pytest.mark.parametrize(
    ("funding", "config_intents", "script", "costs"),
    [
        ("f_none.db", OPEN_CLOSE, (OPEN_T1, CLOSE_T3), None),
        ("f_t3.db", OPEN_CLOSE, (OPEN_T1, CLOSE_T3), None),
        ("f_t3_two.db", OPEN_CLOSE, (OPEN_T1, CLOSE_T3), None),
        ("f_t3.db", OPEN_CLOSE, (OPEN_T1, CLOSE_T3), PROPORTIONAL),
        ("f_t3.db", [intent("OPEN", 1)], (OPEN_T1,), None),
        ("f_t3.db", [intent("OPEN", 1), intent("CLOSE", 4)],
         (OPEN_T1, HedgeIntent(HedgeAction.CLOSE, T4, D(1))), None),
        ("f_t3.db", [], (), None),
    ],
)  # fmt: skip
def test_c2_config_path_equals_the_programmatic_store_runner(
    tmp_path, fx, funding, config_intents, script, costs
):
    config = with_funding(funding, intents=config_intents, costs=costs or base_config()["costs"])
    code, report = run_config(fx, config, tmp_path / "out")
    assert code == 0
    spot_cost, perp_cost = (
        (ProportionalCommissionModel(rate=D("0.001")), ProportionalCommissionModel(rate=D("0.0005")))
        if costs else (ZeroCostModel(), ZeroCostModel())
    )  # fmt: skip
    direct = run_store_backed_multileg_replay(
        programmatic(fx, funding, script, spot_cost, perp_cost)
    )
    parsed = run_store_backed_multileg_replay(load_multileg_config(fx / "config.json").request)
    assert parsed.result == direct.result  # fills, funding, equity, final state, unexecuted, trace
    assert parsed.replay_input_sha256 == direct.replay_input_sha256
    results = dict(report["deterministic"]["results"])
    assert set(results) - set(result_view(direct.result)) == EXTRA_RESULT_KEYS
    for key in EXTRA_RESULT_KEYS:
        results.pop(key)
    assert results == json.loads(canonical_json(result_view(direct.result)))
    assert (
        inputs(report)["replay_input"]["logical_fingerprint_sha256"] == direct.replay_input_sha256
    )


# ================================================================ C3 independent acceptance literals


@pytest.mark.parametrize(
    ("funding", "config_intents", "costs", "expected_equities", "final", "open_at_end"),
    [
        # N1: 400; spot 100 + 101, perp 200 + 0 = 401; 201 + 201 = 402; closed 402
        ("f_none.db", OPEN_CLOSE, None, ["400", "401", "402", "402"], "402", False),
        # N2: -1 * 101 * 0.0001 = -0.0101 paid to the short at T3 -> 402.0101
        ("f_t3.db", OPEN_CLOSE, None, ["400", "401", "402.0101", "402.0101"], "402.0101", False),
        # N3: + 101 * 0.00005 = 0.00505 more -> 402.01515
        ("f_t3_two.db", OPEN_CLOSE, None, ["400", "401", "402.01515", "402.01515"], "402.01515",
         False),
        # N7: open at end, valued at last closes: 100 + 101 + 200 + 1 -> 402
        ("f_none.db", [intent("OPEN", 1)], None, ["400", "401", "402", "402"], "402", True),
        # proportional demo: see test_c1_example_...
        ("f_t3.db", OPEN_CLOSE, PROPORTIONAL, ["400", "400.8490", "401.8591", "401.7076"],
         "401.7076", False),
    ],
)  # fmt: skip
def test_c3_acceptance_values_through_the_config_path(
    tmp_path, fx, funding, config_intents, costs, expected_equities, final, open_at_end
):
    config = with_funding(funding, intents=config_intents, costs=costs or base_config()["costs"])
    code, report = run_config(fx, config, tmp_path / "out")
    results = report["deterministic"]["results"]
    assert code == 0
    assert equities(report) == expected_equities
    assert (results["final_equity"], results["position_open_at_end"]) == (final, open_at_end)


def test_c3_n5_fixed_costs_stay_programmatic_only(tmp_path, fx):
    """N5 (401.7101) uses the test-only FixedCost: it is NOT a config model (see ST2/ST3)."""
    costs = {"spot": {"model": "fixed", "amount": "0.10"}, "perpetual": ZERO}
    with pytest.raises(cli.ConfigError, match="unsupported cost model 'fixed'"):
        load_multileg_config(write_config(fx, base_config(costs=costs)))


# ================================================================ C4 strict config rejection


def _drop(mapping, key):
    return {k: v for k, v in mapping.items() if k != key}


BAD_CONFIGS = [
    (base_config(extra=1), "extra: unknown field"),
    (base_config(config_version=2), "config_version must be the integer 1"),
    (base_config(config_version=True), "config_version must be the integer 1"),
    (base_config(config_version="1"), "config_version must be the integer 1"),
    (base_config(config_kind="research"), 'config_kind must be "multileg_replay"'),
    (_drop(base_config(), "sources"), "missing config field sources"),
    (base_config(sources={"spot": SPOT_SRC}), "missing config field sources.perpetual"),
    (base_config(sources={"spot": SPOT_SRC, "perpetual": "  "}),
     "sources.perpetual must be a non-empty string, got string"),
    (base_config(sources={"spot": None, "perpetual": PERP_SRC}),
     "sources.spot must be a non-empty string, got null"),
    (base_config(sources={"spot": SPOT_SRC, "perpetual": PERP_SRC, "funding": "x"}),
     "sources.funding: the funding store records no source label"),
    (base_config(wallets={"spot_cash": True, "perpetual_collateral": "200"}),
     "wallets.spot_cash must be a decimal string"),
    (base_config(wallets={"spot_cash": 200, "perpetual_collateral": "200"}),
     "wallets.spot_cash must be a decimal string"),
    (base_config(wallets={"spot_cash": "abc", "perpetual_collateral": "200"}),
     "wallets.spot_cash is not a decimal"),
    (base_config(wallets={"spot_cash": "Infinity", "perpetual_collateral": "200"}),
     "wallets.spot_cash must be finite"),
    (base_config(wallets={"spot_cash": "-1", "perpetual_collateral": "200"}),
     "wallets.spot_cash must be >= 0"),
    (_drop(base_config(), "wallets"), "missing config field wallets"),
    (base_config(run_start="2026-01-01T00:00:00"), "run_start must carry an explicit offset"),
    (base_config(run_start="yesterday"), "run_start is not ISO-8601"),
    (base_config(run_start=None), "run_start must be an ISO-8601 string"),
    (base_config(run_start="2026-01-01T00:30:00Z"), "aligned to the '1h' UTC grid"),
    (base_config(run_end=iso(T0)), "run_start must be before run_end"),
    (base_config(as_of=iso(T0 + 3 * H)), "as_of must be >= run_end"),
    (base_config(timeframe="7m"), "timeframe:"),
    (base_config(costs={"spot": {"model": "fixed"}, "perpetual": ZERO}),
     "costs.spot.model: unsupported cost model 'fixed'"),
    (base_config(costs={"spot": {"model": "zero", "rate": "0.1"}, "perpetual": ZERO}),
     "costs.spot.rate: unknown field"),
    (base_config(costs={"spot": {"model": "proportional_commission", "rate": "-0.1"},
                        "perpetual": ZERO}), "costs.spot: rate must be >= 0"),
    (base_config(costs={"spot": {"model": "proportional_commission"}, "perpetual": ZERO}),
     "missing config field costs.spot.rate"),
    (base_config(costs={"spot": {"model": "composite", "components": [{"model": "composite",
                        "components": []}]}, "perpetual": ZERO}),
     r"costs.spot.components\[0\].model: unsupported cost model 'composite'"),
    (base_config(funding_model={"model": "quadratic"}), "unsupported funding model 'quadratic'"),
    (_drop(base_config(), "funding_model"), "missing config field funding_model"),
    (base_config(decimal_context={"prec": 5}), "decimal_context must have exactly the keys"),
    (base_config(stores={"spot": 5, "perpetual": "perp.db", "funding": "f_none.db"}),
     "stores.spot must be a non-empty string, got number"),
    (base_config(pair={"pair_id": "btc", "exchange": "binance", "spot_symbol": "BTCUSDT",
                       "perpetual_symbol": "BTCUSDT", "quote_asset": ""}),
     "pair.quote_asset must be a non-empty string"),
    (base_config(warmup=24), r"warmup: unknown field \(warmup is not supported"),
]  # fmt: skip


@pytest.mark.parametrize(("config", "message"), BAD_CONFIGS)
def test_c4_invalid_config_fields_are_refused(fx, config, message):
    with pytest.raises(cli.ConfigError, match=message):
        load_multileg_config(write_config(fx, config))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            '{"config_kind": "multileg_replay", "config_kind": "x"}',
            "duplicate JSON key 'config_kind'",
        ),
        ('{"wallets": {"spot_cash": 200.5}}', "JSON numbers with a fraction/exponent"),
        ('{"wallets": {"spot_cash": 2e2}}', "JSON numbers with a fraction/exponent"),
        ('{"wallets": {"spot_cash": NaN}}', "JSON constant NaN is not allowed"),
        ('{"wallets": {"spot_cash": Infinity}}', "JSON constant Infinity is not allowed"),
        ("[]", "config must be an object, got array"),
        ("{", "config is not valid JSON"),
    ],
)
def test_c4_json_level_rejections(fx, text, message):
    path = fx / "raw.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(cli.ConfigError, match=message):
        load_multileg_config(path)


def test_c4_early_rejection_never_reaches_stores_or_replay(tmp_path, fx, monkeypatch):
    touched = []
    monkeypatch.setattr(multileg_store, "run_store_backed_multileg_replay",
                        lambda request: touched.append("runner"))  # fmt: skip
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "open_read_only",
                        classmethod(lambda cls, path: touched.append("open")))  # fmt: skip
    for index, (config, _) in enumerate(BAD_CONFIGS[:8]):
        code, report = run_config(fx, config, tmp_path / f"out{index}")
        assert (code, report["status"], report["deterministic"]["results"]) == (1, "failed", None)
        assert report["deterministic"]["errors"][0].startswith("ConfigError: ")
    assert touched == []


def test_c4_missing_config_file_is_a_failed_run(tmp_path):
    code, report = run_cli(tmp_path / "absent.json", tmp_path / "out")
    assert code == 1
    assert report["deterministic"]["errors"] == ["ConfigError: config file not found: absent.json"]


# ================================================================ C5 store-level rejections


def _failed_reason(tmp_path, directory, config, name):
    code, report = run_config(directory, config, tmp_path / name)
    assert (code, report["status"], report["deterministic"]["results"]) == (1, "failed", None)
    (error,) = report["deterministic"]["errors"]
    return error


def test_c5_source_and_price_kind_must_match_exactly(tmp_path, fx):
    wrong = base_config(sources={"spot": SPOT_SRC, "perpetual": "binance:GET https://fapi"})
    assert "[provenance_mismatch] perpetual" in _failed_reason(tmp_path, fx, wrong, "a")
    near = base_config(sources={"spot": SPOT_SRC + " ", "perpetual": PERP_SRC})
    assert "[provenance_mismatch] spot" in _failed_reason(tmp_path, fx, near, "b")
    other = tmp_path / "other"
    other.mkdir()
    write_candles(other / "perp.db", "usdm_perpetual", "index_price", PERP_SRC, acceptance.N_PERP)
    shutil.copy(fx / "spot.db", other / "spot.db")
    shutil.copy(fx / "f_none.db", other / "f_none.db")
    error = _failed_reason(tmp_path, other, base_config(), "c")
    assert "price_kind='index_price'" in error and "[provenance_mismatch]" in error


def test_c5_provenance_less_spot_rows_are_refused(tmp_path, fx):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    store = SQLiteHistoricalCandleStore(legacy / "spot.db")
    store.write_batch([HistoricalCandle("binance", "spot",
                       Candle("BTCUSDT", "1h", T0 + k * H, D(100), D(100), D(100), D(100), D(1)))
                       for k in range(4)])  # fmt: skip
    store.close()
    for name in ("perp.db", "f_none.db"):
        shutil.copy(fx / name, legacy / name)
    assert "[missing_provenance] spot" in _failed_reason(tmp_path, legacy, base_config(), "o")


def test_c5_coverage_and_grid_are_enforced(tmp_path, fx):
    beyond = base_config(run_end=iso(T4 + H), as_of=iso(T4 + H))
    assert "[incomplete_coverage] spot" in _failed_reason(tmp_path, fx, beyond, "a")
    holes = tmp_path / "holes"
    holes.mkdir()
    write_candles(holes / "spot.db", "spot", "spot_trade", SPOT_SRC,
                  [acceptance.N_SPOT[k] for k in (0, 1, 3)], hours=[0, 1, 3])  # fmt: skip
    for name in ("perp.db", "f_none.db"):
        shutil.copy(fx / name, holes / name)
    assert "[missing_data] spot has 3 of 4" in _failed_reason(tmp_path, holes, base_config(), "b")


def test_c5_funding_verified_empty_versus_uncollected_or_other_partition(tmp_path, fx):
    write_funding(fx / "f_uncollected.db", [], cover=None)
    write_funding(fx / "f_eth.db", [], symbol="ETHUSDT")
    write_funding(fx / "f_half.db", [], cover=(T0, T0 + 2 * H))
    for name in ("f_uncollected.db", "f_eth.db", "f_half.db"):
        error = _failed_reason(tmp_path, fx, with_funding(name), name)
        assert "[incomplete_coverage] funding coverage" in error
        assert "never treated as zero" in error
    code, report = run_config(fx, with_funding("f_none.db"), tmp_path / "empty")
    assert code == 0
    assert inputs(report)["funding"]["quality_status"] == "PASS"
    assert inputs(report)["funding"]["event_count"] == 0
    assert report["deterministic"]["results"]["funding_paid"] == "0"


def test_c5_error_reports_do_not_leak_local_paths(tmp_path, fx):
    wrong = base_config(sources={"spot": SPOT_SRC, "perpetual": "synthetic:other"})
    run_config(fx, wrong, tmp_path / "out")
    for name in ("report.json", "report.md"):
        text = (tmp_path / "out" / name).read_text(encoding="utf-8")
        assert str(tmp_path) not in text and str(Path.home()) not in text


# ================================================================ C6 intents: refused / unexecuted / no trade


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ([intent("OPEN", 1), intent("OPEN", 2)], r"\[OPEN\] or \[OPEN, CLOSE\]"),
        ([intent("CLOSE", 1)], r"\[OPEN\] or \[OPEN, CLOSE\]"),
        ([intent("OPEN", 1), intent("CLOSE", 2), intent("OPEN", 3)], "no reversal"),
        ([intent("OPEN", 2), intent("CLOSE", 2)], "strictly later"),
        ([intent("OPEN", 3), intent("CLOSE", 1)], "strictly later"),
        ([intent("OPEN", 1, "1"), intent("CLOSE", 3, "2")], "no partial close"),
        ([intent("REDUCE", 1)], 'action must be "OPEN" or "CLOSE"'),
        ([intent("OPEN", 1, "0")], "quantity must be > 0"),
        ([intent("OPEN", 1, "-1")], "quantity must be > 0"),
        ([{"action": "OPEN", "decision_time": iso(T0 + H), "quantity": 1}],
         "quantity must be a decimal string"),
        ([intent("OPEN", 0)], "availability instant"),
        ([intent("OPEN", 5)], "availability instant"),
        ([{"action": "OPEN", "decision_time": "2026-01-01T01:30:00Z", "quantity": "1"}],
         "availability instant"),
        ([{"action": "OPEN", "decision_time": iso(T0 + H), "quantity": "1", "leverage": "3"}],
         r"intents\[0\].leverage: unknown field"),
        ({"action": "OPEN"}, "intents must be an array, got object"),
    ],
)  # fmt: skip
def test_c6_unsupported_intent_scripts_are_refused(fx, script, message):
    with pytest.raises(cli.ConfigError, match=message):
        load_multileg_config(write_config(fx, base_config(intents=script)))


def test_c6_empty_script_is_a_valid_flat_no_trade_result(tmp_path, fx):
    code, report = run_config(fx, base_config(intents=[]), tmp_path / "out")
    results = report["deterministic"]["results"]
    assert (code, report["status"], report["warning_count"]) == (0, "succeeded", 0)
    assert (results["paired_fill_count"], results["fills"], results["lifecycle_at_end"]) == (
        0, [], "FLAT",
    )  # fmt: skip
    assert equities(report) == ["400", "400", "400", "400"]
    assert results["unexecuted_intents"] == []
    assert "scripts no intents" in results["no_trade_explanation"]
    assert "not a data error" in results["no_trade_explanation"]


def test_c6_last_candle_intent_is_reported_unexecuted_not_filled(tmp_path, fx):
    code, report = run_config(fx, base_config(intents=[intent("OPEN", 4)]), tmp_path / "a")
    results = report["deterministic"]["results"]
    assert (code, report["status"], report["warning_count"]) == (0, "succeeded", 1)
    assert checks(report)["intents.execution"] == "warning"
    assert (results["fills"], results["lifecycle_at_end"]) == ([], "FLAT")
    assert [u["action"] for u in results["unexecuted_intents"]] == ["OPEN"]
    assert "last candle" in results["no_trade_explanation"]
    code, report = run_config(
        fx, base_config(intents=[intent("OPEN", 1), intent("CLOSE", 4)]), tmp_path / "b"
    )
    results = report["deterministic"]["results"]
    assert (code, report["warning_count"]) == (0, 2)
    assert checks(report) | {} == {
        "inputs.provenance_and_coverage": "passed",
        "identity.replay_input": "passed",
        "intents.execution": "warning",
        "position.at_end": "warning",
    }
    # the CLOSE never filled: still open, N7 values 402 with unrealized 1 + 1, realized 0 + 0
    assert (results["final_equity"], results["position_open_at_end"]) == ("402", True)
    assert results["unrealized_pnl_at_end"] == {"spot": "1", "perpetual": "1"}
    assert results["realized_pnl"] == {"spot": "0", "perpetual": "0"}
    assert results["no_trade_explanation"] is None


# ================================================================ C7 cwd / relocation independence


def test_c7_cwd_and_output_path_do_not_change_the_semantic_result(tmp_path, fx, monkeypatch):
    config_path = write_config(fx, with_funding("f_t3.db"))
    _, first = run_cli(config_path, tmp_path / "a")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _, second = run_cli(Path(os.path.relpath(config_path, elsewhere)), tmp_path / "deep-b")
    assert second["status"] == "succeeded"
    for key in ("deterministic_sha256",):
        assert first[key] == second[key]
    assert first["deterministic"]["run_input_sha256"] == second["deterministic"]["run_input_sha256"]
    assert not list(elsewhere.iterdir())  # nothing opened or created relative to the cwd


def test_c7_moved_fixture_gives_the_same_result_and_real_source_changes_are_kept(tmp_path, fx):
    config = with_funding("f_t3.db", costs=PROPORTIONAL)
    _, original = run_config(fx, config, tmp_path / "a")
    moved = shutil.copytree(fx, tmp_path / "somewhere" / "else")
    _, again = run_config(moved, config, tmp_path / "b")
    assert again["deterministic_sha256"] == original["deterministic_sha256"]
    relabeled = build_fixture(tmp_path / "relabeled", spot_src="synthetic:other/spot")
    config_other = config | {"sources": {"spot": "synthetic:other/spot", "perpetual": PERP_SRC}}
    _, other = run_config(relabeled, config_other, tmp_path / "c")
    a, b = original["deterministic"], other["deterministic"]
    provenance_keys = {"resolved_config", "input_evidence", "multileg_input_sha256"}
    for key in provenance_keys:  # the relabeled provenance is kept, never hidden
        assert b["results"][key] != a["results"][key], key
    economics = {k: v for k, v in a["results"].items() if k not in provenance_keys}
    assert {k: v for k, v in b["results"].items() if k not in provenance_keys} == economics
    assert inputs(other)["spot"]["registered_dataset"]["source"] == "synthetic:other/spot"
    assert b["run_input_sha256"] != a["run_input_sha256"]
    assert other["deterministic_sha256"] != original["deterministic_sha256"]


# ================================================================ C8 input identity sensitivity


def _ids(tmp_path, directory, config, name):
    code, report = run_config(directory, config, tmp_path / name, f"{name}.json")
    assert code == 0, report["deterministic"]["errors"]
    return (
        inputs(report)["replay_input"]["logical_fingerprint_sha256"],
        report["deterministic"]["run_input_sha256"],
    )


def test_c8_each_consumed_input_changes_the_replay_identity(tmp_path, fx):
    base = with_funding("f_t3.db", costs=PROPORTIONAL)
    reference = _ids(tmp_path, fx, base, "base")
    assert _ids(tmp_path, fx, base, "base-again") == reference
    write_funding(fx / "f_t3_other.db", [event(acceptance.T3, "0.0002")])
    changes = {
        "funding_value": with_funding("f_t3_other.db", costs=PROPORTIONAL),
        "intent_time": base | {"intents": [intent("OPEN", 1), intent("CLOSE", 2)]},
        "quantity": base | {"intents": [intent("OPEN", 1, "1.5"), intent("CLOSE", 3, "1.5")]},
        "wallet": base | {"wallets": {"spot_cash": "300", "perpetual_collateral": "200"}},
        "cost_param": base | {"costs": {**PROPORTIONAL, "spot": {"model": "proportional_commission",
                                                             "rate": "0.002"}}},
        "as_of": base | {"as_of": iso(T4 + H)},
        "decimal_context": base | {"decimal_context": {**normalize_decimal_context(None),
                                                       "prec": 50}},
    }  # fmt: skip
    for name, config in changes.items():
        replay_id, run_id = _ids(tmp_path, fx, config, name)
        assert replay_id != reference[0], name
        assert run_id != reference[1], name
    rows = list(acceptance.N_SPOT)
    rows[2] = ("101", "100.5")
    price = build_fixture(tmp_path / "price", spot_rows=rows)
    assert _ids(tmp_path, price, base, "price-run")[0] != reference[0]


def test_c8_source_label_changes_the_run_identity_not_the_replay_identity(tmp_path, fx):
    base = with_funding("f_t3.db")
    reference = _ids(tmp_path, fx, base, "base")
    relabeled = build_fixture(tmp_path / "relabeled", perp_src="synthetic:other/perp")
    config = base | {"sources": {"spot": SPOT_SRC, "perpetual": "synthetic:other/perp"}}
    replay_id, run_id = _ids(tmp_path, relabeled, config, "relabeled-run")
    assert replay_id == reference[0]  # same consumed values and parameters
    assert run_id != reference[1]  # declared provenance is part of the run identity


def test_c8_unqueried_rows_and_output_paths_do_not_change_consumed_identity(tmp_path, fx):
    config = with_funding("f_t3.db")
    code, before = run_config(fx, config, tmp_path / "a")
    store = SQLiteHistoricalCandleStore(fx / "spot.db")
    store.write_ingestion_batch(
        [HistoricalCandle("binance", "spot", Candle("BTCUSDT", "1h", T4, D(101), D(150), D(101),
                          D(150), D(1)))],
        dataset=CandleDataset("binance", "spot", "BTCUSDT", "1h", "spot_trade", SPOT_SRC),
        covered_start=T4, covered_end=T4 + H,
    )  # fmt: skip
    store.close()
    code, after = run_config(fx, config, tmp_path / "different-output")
    assert code == 0
    for role in ("spot", "replay_input"):
        assert (inputs(after)[role]["logical_fingerprint_sha256"]
                == inputs(before)[role]["logical_fingerprint_sha256"])  # fmt: skip
    assert after["deterministic"]["results"] == before["deterministic"]["results"]


# ================================================================ C9 unknown models / identity failures


def test_c9_legacy_programmatic_none_identity_is_kept(fx):
    class Unknown:
        def calculate_cost(self, *, quantity, execution_price):
            return D(0)

    request = programmatic(fx, "f_none.db", (OPEN_T1, CLOSE_T3), Unknown(), ZeroCostModel())
    assert run_store_backed_multileg_replay(request).replay_input_sha256 is None


def test_c9_missing_identity_is_never_a_reproducible_success(tmp_path, fx, monkeypatch):
    monkeypatch.setattr(multileg_store, "describe_cost_model", lambda model: None)
    code, report = run_config(fx, base_config(), tmp_path / "none")
    assert (code, report["status"]) == (1, "failed")
    assert checks(report)["identity.replay_input"] == "failed"
    assert inputs(report)["replay_input"]["logical_fingerprint_sha256"] is None


def test_c9_identity_errors_fail_the_run(tmp_path, fx, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("fingerprint unavailable")

    monkeypatch.setattr(multileg_store, "replay_input_fingerprint", broken)
    code, report = run_config(fx, base_config(), tmp_path / "err")
    assert (code, report["status"], report["deterministic"]["results"]) == (1, "failed", None)
    assert report["deterministic"]["errors"] == ["RuntimeError: fingerprint unavailable"]


# ================================================================ C10 read-only, no overwrite, aliases


def test_c10_sources_and_config_stay_unchanged_after_success_and_failure(tmp_path, fx):
    write_config(fx, with_funding("f_t3.db"))
    write_config(fx, base_config(sources={"spot": SPOT_SRC, "perpetual": "x:y"}), "bad.json")
    before = files(fx)
    assert run_cli(fx / "config.json", tmp_path / "ok")[0] == 0
    assert run_cli(fx / "bad.json", tmp_path / "bad")[0] == 1
    assert files(fx) == before  # bytes, mtimes, and no -wal/-shm/-journal files
    for name in ("spot.db", "perp.db", "f_t3.db", "f_none.db"):  # connections released
        os.replace(fx / name, fx / f"moved-{name}")


def test_c10_missing_store_is_not_created(tmp_path, fx):
    code, report = run_config(fx, with_funding("absent.db"), tmp_path / "out")
    assert code == 1
    missing = "stores.funding: file not found: absent.db (read-only runs never create stores)"
    assert report["deterministic"]["errors"] == [f"ConfigError: {missing}"]
    assert not (fx / "absent.db").exists()


def test_c10_existing_output_is_never_overwritten(tmp_path, fx, capsys):
    config_path = write_config(fx, base_config())
    assert run_cli(config_path, tmp_path / "out")[0] == 0
    first = (tmp_path / "out" / "report.json").read_bytes()
    argv = ["multileg-replay", "--config", str(config_path), "--output", str(tmp_path / "out")]
    assert cli.main(argv) == 2
    assert (tmp_path / "out" / "report.json").read_bytes() == first
    argv[-1] = str(tmp_path / "no-parent" / "out")
    assert cli.main(argv) == 2
    assert cli.main(["multileg-example", "--output", str(fx)]) == 2  # existing directory
    assert "error:" in capsys.readouterr().err


def test_c10_path_aliases_are_refused(fx):
    same = base_config(stores={"spot": "spot.db", "perpetual": "./spot.db", "funding": "f_none.db"})
    with pytest.raises(cli.ConfigError, match="two roles point to the same file"):
        load_multileg_config(write_config(fx, same))
    os.link(fx / "perp.db", fx / "perp-link.db")
    linked = base_config(
        stores={"spot": "spot.db", "perpetual": "perp-link.db", "funding": "perp.db"}
    )
    with pytest.raises(cli.ConfigError, match="same physical file"):
        load_multileg_config(write_config(fx, linked))


# ================================================================ C11 network / Decimal / import isolation


def test_c11_offline_run_with_network_blocked_and_hostile_ambient_context(
    tmp_path, fx, monkeypatch
):
    def no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    config_path = write_config(fx, with_funding("f_t3.db", costs=PROPORTIONAL))
    _, reference = run_cli(config_path, tmp_path / "ref")
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        code, hostile = run_cli(config_path, tmp_path / "hostile")
        bad = write_config(
            fx, base_config(sources={"spot": SPOT_SRC, "perpetual": "x:y"}), "b.json"
        )
        assert run_cli(bad, tmp_path / "bad")[0] == 1
        assert run_cli(fx / "absent.json", tmp_path / "absent")[0] == 1
        assert (decimal.getcontext().prec, decimal.getcontext().rounding) == (3, ROUND_DOWN)
    assert code == 0
    assert hostile["deterministic_sha256"] == reference["deterministic_sha256"]
    assert hostile["deterministic"]["results"]["final_equity"] == "401.7076"


def test_c11_import_has_no_side_effects(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("side effect during import")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sqlite3, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(cli, "main", refuse)
    importlib.reload(multileg_config)
    assert list(tmp_path.iterdir()) == []


# ================================================================ C12 existing entry points


def test_c12_existing_commands_are_still_registered():
    commands = cli.build_parser()._subparsers._group_actions[0].choices
    assert {"doctor", "inspect", "basis-report", "funding-research", "offline-smoke",
            "public-smoke", "multileg-replay", "multileg-example",
            "multileg-doctor"} == set(commands)  # fmt: skip


def test_c6_accounting_rejection_is_a_failed_run_not_a_data_or_no_trade_result(tmp_path, fx):
    too_big = base_config(intents=[intent("OPEN", 1, "3"), intent("CLOSE", 3, "3")])
    code, report = run_config(fx, too_big, tmp_path / "out")
    # 3 x 100 = 300 > 200 spot cash: refused by the accounting, after valid store checks
    assert (code, report["status"], report["deterministic"]["results"]) == (1, "failed", None)
    (error,) = report["deterministic"]["errors"]
    assert error.startswith("ValueError: insufficient spot cash")
