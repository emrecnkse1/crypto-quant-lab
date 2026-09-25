"""Declared USDⓈ-M source labels and the synthetic fixture fix (FUNDING_RESEARCH_SPEC.md Bölüm 19.14).

P1-P11 of the acceptance matrix. No network: pages come from fake transports
or a patched urlopen; expected labels and hashes are written literally.
"""

import dataclasses
import decimal
import io
import json
import socket
import sqlite3
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel
from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.data_quality.usdm_ingestion import (
    ingest_binance_usdm_index_price_klines,
    ingest_binance_usdm_perpetual_klines,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.binance_usdm import (
    parse_binance_usdm_index_price_kline,
    parse_binance_usdm_kline,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import cli, multileg_offline
from crypto_quant_lab.research.basis import load_close_basis_history
from crypto_quant_lab.research.funding_carry import (
    funding_carry_candidate,
    load_funding_signal_history,
)
from crypto_quant_lab.research.offline_fixture import (
    FIXTURE_CONTRACT_SOURCE,
    FIXTURE_INDEX_SOURCE,
    FIXTURE_START,
    FIXTURE_SYMBOL,
    build_offline_fixture,
    fixture_config,
)
from crypto_quant_lab.research.report import canonical_json, sha256_hex
from crypto_quant_lab.research.usdm_perpetual import evaluate_usdm_perpetual_funding_research
from crypto_quant_lab.storage.base import DataConflictError, HistoricalCandle
from crypto_quant_lab.storage.datasets import (
    BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
    BINANCE_USDM_KLINES_SOURCE,
    CandleCoverageInterval,
    CandleDataset,
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.windows import TemporalWindow

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = 1767225600000
H = timedelta(hours=1)
H_MS = 3_600_000
AS_OF = T0 + 10 * H
NS = ("binance", "usdm_perpetual", "BTCUSDT", "1h")
CONTRACT_LABEL = "synthetic:test-usdm/contract-trade/v1"
INDEX_LABEL = "synthetic:test-usdm/index-price/v1"
FAPI_KLINES = "binance:GET https://fapi.binance.com/fapi/v1/klines"
FAPI_INDEX = "binance:GET https://fapi.binance.com/fapi/v1/indexPriceKlines"
# config_sha256 of the offline-smoke config BEFORE Bölüm 19.14 (recorded 2026-09-25
# from the pre-change run); a config without `sources` must still hash to it.
PRE_19_14_FIXTURE_CONFIG_SHA256 = "7de15e518239fe4cd0c463ee6cff197e3605d32f8ed59047ffcb2f3e28397fd3"

ROLES = {
    "contract": (ingest_binance_usdm_perpetual_klines, "symbol", parse_binance_usdm_kline),
    "index": (ingest_binance_usdm_index_price_klines, "pair", parse_binance_usdm_index_price_kline),
}


def row(k, close="100"):
    open_ms = T0_MS + k * H_MS
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [open_ms, "100", high, low, close, "1", open_ms + H_MS - 1, "100", 7, "0.5", "50", "0"]


def fake(role, rows, *, fail_after=None):
    parse = ROLES[role][2]
    calls = []

    def fetch(*, start_time_ms, end_time_ms):
        calls.append(start_time_ms)
        if fail_after is not None and len(calls) > fail_after:
            raise ConnectionError("transport dropped")
        page = [r for r in rows if start_time_ms <= r[0] <= end_time_ms][:2]
        return [parse(r, "BTCUSDT", "1h") for r in page]

    fetch.calls = calls
    return fetch


def ingest(role, store, fetch=None, *, end=T0 + 4 * H, **kwargs):
    function, symbol_kw, _ = ROLES[role]
    return function(
        store,
        **{symbol_kw: "BTCUSDT"},
        timeframe="1h",
        requested_start=T0,
        requested_end=end,
        as_of_time=AS_OF,
        fetch_page=fetch,
        **kwargs,
    )


def untouched(store):
    return (
        store.query_dataset(*NS) is None
        and store.query_coverage(*NS, T0, T0 + 10 * H) == []
        and store.query(*NS, T0, T0 + 10 * H) == []
    )


def label_for(role):
    return CONTRACT_LABEL if role == "contract" else INDEX_LABEL


# ================================================================ P1 default real transport


@pytest.mark.parametrize(
    ("role", "url_prefix", "canonical"),
    [
        ("contract", "https://fapi.binance.com/fapi/v1/klines?", FAPI_KLINES),
        ("index", "https://fapi.binance.com/fapi/v1/indexPriceKlines?", FAPI_INDEX),
    ],
)
def test_p1_default_real_adapter_keeps_the_canonical_endpoint_label(
    tmp_path, monkeypatch, role, url_prefix, canonical
):
    seen = []

    def urlopen(url, timeout):
        seen.append(url)
        return io.BytesIO(json.dumps([row(0), row(1)]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    ingest(role, store, end=T0 + 2 * H)
    assert seen and all(url.startswith(url_prefix) for url in seen)
    assert store.query_dataset(*NS).source == canonical
    assert canonical == (BINANCE_USDM_KLINES_SOURCE if role == "contract"
                         else BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE)  # fmt: skip
    store.close()


# ================================================================ P2 explicit synthetic label


@pytest.mark.parametrize("role", ["contract", "index"])
def test_p2_declared_source_is_recorded_exactly_through_real_ingestion(tmp_path, role):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    fetch = fake(role, [row(k) for k in range(4)])
    result = ingest(role, store, fetch, source=label_for(role))
    kind = "contract_trade" if role == "contract" else "index_price"
    assert result.candle_count == 4
    assert store.query_dataset(*NS) == CandleDataset(
        "binance", "usdm_perpetual", "BTCUSDT", "1h", kind, label_for(role)
    )
    assert store.query_coverage(*NS, T0, T0 + 10 * H) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + 4 * H)
    ]
    assert len(fetch.calls) == 2
    store.close()


def test_p2_dataset_builders_default_to_canonical_and_accept_a_declared_source():
    assert binance_usdm_perpetual_contract_trade_dataset("BTCUSDT", "1h").source == FAPI_KLINES
    assert binance_usdm_index_price_dataset("BTCUSDT", "1h").source == FAPI_INDEX
    assert binance_usdm_perpetual_contract_trade_dataset(
        "BTCUSDT", "1h", source=CONTRACT_LABEL
    ).source == CONTRACT_LABEL  # fmt: skip
    assert binance_usdm_index_price_dataset(
        "BTCUSDT", "1h", source=INDEX_LABEL
    ).source == INDEX_LABEL  # fmt: skip


# ================================================================ P3 invalid labels, no fetch


@pytest.mark.parametrize("role", ["contract", "index"])
@pytest.mark.parametrize(
    ("bad", "error", "message"),
    [
        ("", ValueError, "source cannot be empty"),
        ("   ", ValueError, "source cannot be empty"),
        ("\t\n", ValueError, "source cannot be empty"),
        (123, TypeError, "source must be a str, got int"),
        (b"synthetic:x", TypeError, "source must be a str, got bytes"),
    ],
)
def test_p3_invalid_source_is_refused_before_any_fetch_or_write(
    tmp_path, role, bad, error, message
):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    fetch = fake(role, [row(k) for k in range(4)])
    with pytest.raises(error, match=message):
        ingest(role, store, fetch, source=bad)
    assert fetch.calls == []
    assert untouched(store)
    store.close()


@pytest.mark.parametrize("role", ["contract", "index"])
def test_p3_real_transport_with_a_declared_source_is_refused_before_any_request(
    tmp_path, monkeypatch, role
):
    requests = []

    def urlopen(url, timeout):  # pragma: no cover - must never run
        requests.append(url)
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    with pytest.raises(ValueError, match="source is fixed by the real USDⓈ-M adapter"):
        ingest(role, store, source=label_for(role))
    with pytest.raises(ValueError, match="source is fixed by the real USDⓈ-M adapter"):
        ingest(role, store, source=label_for(role).replace("synthetic:", "binance:"))
    assert requests == []
    assert untouched(store)
    store.close()


# ================================================================ P4 legacy limitation


@pytest.mark.parametrize(("role", "canonical"), [("contract", FAPI_KLINES), ("index", FAPI_INDEX)])
def test_p4_legacy_injected_transport_without_source_keeps_the_canonical_label(
    tmp_path, role, canonical
):
    """KNOWN LEGACY LIMITATION (Bölüm 19.14): kept for existing callers, not endorsed.

    A replaced transport that declares nothing is still recorded under the
    canonical endpoint label although no request reached it. The fixed offline
    fixture no longer relies on this (see test_p4_the_offline_fixture_...).
    """
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    ingest(role, store, fake(role, [row(k) for k in range(4)]))
    assert store.query_dataset(*NS).source == canonical
    store.close()


def test_p4_the_offline_fixture_declares_synthetic_sources_in_stores_and_config(tmp_path):
    assert FIXTURE_CONTRACT_SOURCE == "synthetic:offline-fixture/contract-trade/v1"
    assert FIXTURE_INDEX_SOURCE == "synthetic:offline-fixture/index-price/v1"
    fixture = build_offline_fixture(tmp_path / "fx")
    ns = ("binance", "usdm_perpetual", FIXTURE_SYMBOL, "1h")
    for db, kind, source in (("contract.db", "contract_trade", FIXTURE_CONTRACT_SOURCE),
                             ("index.db", "index_price", FIXTURE_INDEX_SOURCE)):  # fmt: skip
        store = SQLiteHistoricalCandleStore(fixture.directory / db)
        assert store.query_dataset(*ns) == CandleDataset(*ns, kind, source)
        store.close()
    assert fixture.config["sources"] == {
        "contract": FIXTURE_CONTRACT_SOURCE,
        "index": FIXTURE_INDEX_SOURCE,
    }
    assert fixture_config()["sources"] == fixture.config["sources"]


def test_p4_multileg_offline_perpetual_is_labeled_synthetic():
    assert multileg_offline._PERP_DS.source == "synthetic:perpetual"
    assert multileg_offline._SPOT_DS.source == "synthetic:spot"
    for manifest in multileg_offline._input_manifest():
        assert "binance:GET" not in json.dumps(manifest)


# ================================================================ P5 store invariants


@pytest.mark.parametrize("role", ["contract", "index"])
def test_p5_failed_or_invalid_pagination_writes_nothing(tmp_path, role):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    with pytest.raises(ConnectionError, match="transport dropped"):
        ingest(role, store, fake(role, [row(k) for k in range(4)], fail_after=1),
               source=label_for(role), max_attempts=1)  # fmt: skip
    assert untouched(store)
    with pytest.raises(ValueError, match="not strictly ascending"):
        ingest(role, store, fake(role, [row(1), row(0), row(2), row(3)]), source=label_for(role))
    assert untouched(store)
    store.close()


@pytest.mark.parametrize("role", ["contract", "index"])
def test_p5_empty_complete_range_records_authoritative_absence_with_the_label(tmp_path, role):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    result = ingest(role, store, fake(role, []), source=label_for(role))
    assert (result.candle_count, result.leading_absent_count) == (0, 4)
    assert store.query_dataset(*NS).source == label_for(role)
    assert store.query_coverage(*NS, T0, T0 + 10 * H) == [
        CandleCoverageInterval(start_time=T0, end_time=T0 + 4 * H)
    ]
    assert store.query(*NS, T0, T0 + 10 * H) == []
    store.close()


@pytest.mark.parametrize("role", ["contract", "index"])
def test_p5_idempotent_retry_and_source_conflicts(tmp_path, role):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    rows = [row(k) for k in range(4)]
    ingest(role, store, fake(role, rows), source=label_for(role))
    ingest(role, store, fake(role, rows), source=label_for(role))  # same content: no change
    assert len(store.query(*NS, T0, T0 + 10 * H)) == 4
    assert len(store.query_coverage(*NS, T0, T0 + 10 * H)) == 1
    with pytest.raises(DataConflictError, match="is registered as"):
        ingest(role, store, fake(role, rows), source="synthetic:other")
    with pytest.raises(DataConflictError, match="is registered as"):
        ingest(role, store, fake(role, rows))  # legacy canonical label vs declared label
    with pytest.raises(DataConflictError):
        ingest(role, store, fake(role, [row(k, "101") for k in range(4)]), source=label_for(role))
    assert store.query_dataset(*NS).source == label_for(role)
    assert [r.candle.close for r in store.query(*NS, T0, T0 + 10 * H)] == [Decimal(100)] * 4
    store.close()


def test_p5_canonical_store_is_never_relabeled_by_a_declared_source(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    ingest("contract", store, fake("contract", [row(k) for k in range(4)]))  # legacy label
    with pytest.raises(DataConflictError, match="is registered as"):
        ingest("contract", store, fake("contract", [row(k) for k in range(4)]),
               source=CONTRACT_LABEL)  # fmt: skip
    assert store.query_dataset(*NS).source == FAPI_KLINES
    store.close()


def test_p5_provenance_less_rows_are_never_relabeled(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "s.db")
    candle = Candle(symbol="BTCUSDT", timeframe="1h", open_time=T0, open=Decimal(100),
                    high=Decimal(100), low=Decimal(100), close=Decimal(100),
                    volume=Decimal(1))  # fmt: skip
    store.write_batch([HistoricalCandle(exchange="binance", market_type="usdm_perpetual",
                                        candle=candle)])  # fmt: skip
    with pytest.raises(DataConflictError, match="unknown provenance"):
        ingest("contract", store, fake("contract", [row(k) for k in range(4)]),
               source=CONTRACT_LABEL)  # fmt: skip
    assert store.query_dataset(*NS) is None
    assert store.query_coverage(*NS, T0, T0 + 10 * H) == []
    store.close()


# ================================================================ P6 config <-> reader matching


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    return build_offline_fixture(tmp_path_factory.mktemp("src") / "fixture").directory


@pytest.fixture(scope="module")
def canonical_fixture_dir(tmp_path_factory, fixture_dir):
    """Same rows as the fixture, but written the legacy way (canonical labels)."""
    directory = tmp_path_factory.mktemp("canon")
    ns = ("binance", "usdm_perpetual", FIXTURE_SYMBOL, "1h")
    for db, dataset in (
        ("contract.db", binance_usdm_perpetual_contract_trade_dataset(FIXTURE_SYMBOL, "1h")),
        ("index.db", binance_usdm_index_price_dataset(FIXTURE_SYMBOL, "1h")),
    ):
        source = SQLiteHistoricalCandleStore(fixture_dir / db)
        records = source.query(*ns, FIXTURE_START, FIXTURE_START + 24 * H)
        coverage = source.query_coverage(*ns, FIXTURE_START, FIXTURE_START + 24 * H)
        source.close()
        target = SQLiteHistoricalCandleStore(directory / db)
        target.write_ingestion_batch(records, dataset=dataset, covered_start=coverage[0].start_time,
                                     covered_end=coverage[0].end_time)  # fmt: skip
        target.close()
    (directory / "funding.db").write_bytes((fixture_dir / "funding.db").read_bytes())
    return directory


def _config(directory, tmp_path, *, sources, name="c.json"):
    config = fixture_config()
    config["stores"] = {role: str(directory / f"{role}.db")
                        for role in ("contract", "index", "funding")}  # fmt: skip
    config.pop("sources")
    if sources is not None:
        config["sources"] = sources
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


FIXTURE_SOURCES = {"contract": FIXTURE_CONTRACT_SOURCE, "index": FIXTURE_INDEX_SOURCE}


def _doctor_failures(path):
    return {name for status, name, _ in cli.doctor(path, None) if status == "FAIL"}


def test_p6_matching_config_passes_and_mismatches_fail_in_both_directions(
    tmp_path, fixture_dir, canonical_fixture_dir
):
    provenance = {"contract.provenance", "index.provenance"}
    # synthetic stores + synthetic config / canonical stores + canonical (default) config: pass
    assert not _doctor_failures(_config(fixture_dir, tmp_path, sources=FIXTURE_SOURCES))
    assert not _doctor_failures(_config(canonical_fixture_dir, tmp_path, sources=None, name="d"))
    # synthetic stores + default canonical config: refused (the pre-19.14 label is not accepted)
    assert provenance <= _doctor_failures(_config(fixture_dir, tmp_path, sources=None, name="e"))
    # canonical stores + synthetic config: refused
    assert provenance <= _doctor_failures(
        _config(canonical_fixture_dir, tmp_path, sources=FIXTURE_SOURCES, name="f")
    )
    # one role swapped: only that role fails
    half = {"contract": FIXTURE_CONTRACT_SOURCE}
    assert _doctor_failures(_config(fixture_dir, tmp_path, sources=half, name="g")) >= {
        "index.provenance"
    }
    assert "contract.provenance" not in _doctor_failures(
        _config(fixture_dir, tmp_path, sources=half, name="g2")
    )


@pytest.mark.parametrize("command", ["inspect", "basis-report", "funding-research"])
def test_p6_research_commands_refuse_a_source_mismatch(
    tmp_path, fixture_dir, canonical_fixture_dir, command
):
    ok = _config(fixture_dir, tmp_path, sources=FIXTURE_SOURCES, name="ok.json")
    assert cli.main([command, "--config", str(ok), "--output", str(tmp_path / "ok")]) == 0
    for name, directory, sources in (("a", fixture_dir, None),
                                     ("b", canonical_fixture_dir, FIXTURE_SOURCES)):  # fmt: skip
        path = _config(directory, tmp_path, sources=sources, name=f"{name}.json")
        out = tmp_path / f"out-{name}"
        assert cli.main([command, "--config", str(path), "--output", str(out)]) == 1
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert "registered as" in json.dumps(report)


def test_p6_readers_compare_the_expected_source_exactly(fixture_dir):
    contract = SQLiteHistoricalCandleStore(fixture_dir / "contract.db")
    index = SQLiteHistoricalCandleStore(fixture_dir / "index.db")
    kwargs = {"symbol": FIXTURE_SYMBOL, "timeframe": "1h",
              "start_time": FIXTURE_START, "end_time": FIXTURE_START + 24 * H}  # fmt: skip
    history = load_close_basis_history(contract, index, contract_source=FIXTURE_CONTRACT_SOURCE,
                                       index_source=FIXTURE_INDEX_SOURCE, **kwargs)  # fmt: skip
    assert len(history.visible_at(FIXTURE_START + 24 * H)) == 23
    for bad in ({}, {"contract_source": FIXTURE_CONTRACT_SOURCE},
                {"index_source": FIXTURE_INDEX_SOURCE},
                {"contract_source": FIXTURE_CONTRACT_SOURCE + " ",
                 "index_source": FIXTURE_INDEX_SOURCE},
                {"contract_source": FIXTURE_CONTRACT_SOURCE.upper(),
                 "index_source": FIXTURE_INDEX_SOURCE}):  # fmt: skip
        with pytest.raises(ValueError, match="registered as"):
            load_close_basis_history(contract, index, **bad, **kwargs)
    contract.close()
    index.close()


def _funding_trial(fixture_dir, **source):
    contract = SQLiteHistoricalCandleStore(fixture_dir / "contract.db")
    funding = SQLiteHistoricalFundingStore(fixture_dir / "funding.db")
    try:
        history = load_funding_signal_history(
            funding, exchange="binance", market_type="usdm_perpetual", symbol=FIXTURE_SYMBOL,
            coverage_start=FIXTURE_START - H * 8, coverage_end=FIXTURE_START + H * 24,
            publication_lag=timedelta(seconds=60),
        )  # fmt: skip
        candidate = funding_carry_candidate(
            "c", short_entry_rate=Decimal("0.0002"), long_entry_rate=Decimal("-0.0001"),
            max_funding_age=timedelta(hours=9), publication_lag=timedelta(seconds=60),
        )  # fmt: skip
        return evaluate_usdm_perpetual_funding_research(
            contract, funding, history, candidate,
            windows=(TemporalWindow(start=FIXTURE_START, end=FIXTURE_START + H * 12),),
            timeframe="1h", as_of_time=FIXTURE_START + H * 48,
            config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
            cost_model=ProportionalCommissionModel(rate=Decimal("0.001")),
            funding_model=LinearFundingModel(), **source,
        )  # fmt: skip
    finally:
        contract.close()
        funding.close()


def test_p6_funding_reader_requires_the_declared_contract_source(fixture_dir):
    assert _funding_trial(fixture_dir, contract_source=FIXTURE_CONTRACT_SOURCE) is not None
    with pytest.raises(ValueError, match="registered as"):
        _funding_trial(fixture_dir)
    with pytest.raises(ValueError, match="registered as"):
        _funding_trial(fixture_dir, contract_source=FIXTURE_INDEX_SOURCE)


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        ([FIXTURE_CONTRACT_SOURCE], "sources must be an object"),
        ("synthetic:x", "sources must be an object"),
        ({"funding": "synthetic:x"}, r"sources.funding: unknown role"),
        ({"spot": "synthetic:x"}, r"sources.spot: unknown role"),
        ({"contract": ""}, r"sources.contract must be a non-empty string"),
        ({"contract": "   "}, r"sources.contract must be a non-empty string"),
        ({"index": 7}, r"sources.index must be a non-empty string"),
        ({"index": None}, r"sources.index must be a non-empty string"),
    ],
)
def test_p6_invalid_sources_config_is_refused(tmp_path, fixture_dir, sources, message):
    path = _config(fixture_dir, tmp_path, sources=sources)
    with pytest.raises(cli.ConfigError, match=message):
        cli.load_config(path)


def test_p6_omitted_sources_resolve_to_the_canonical_endpoints(tmp_path, fixture_dir):
    config = cli.load_config(_config(fixture_dir, tmp_path, sources=None))
    assert config.sources == {"contract": FAPI_KLINES, "index": FAPI_INDEX}
    assert config.expected_dataset("contract") == binance_usdm_perpetual_contract_trade_dataset(
        FIXTURE_SYMBOL, "1h"
    )
    assert config.expected_dataset("index") == binance_usdm_index_price_dataset(
        FIXTURE_SYMBOL, "1h"
    )
    assert "sources" not in config.effective()
    with pytest.raises(ValueError, match="unknown candle role 'funding'"):
        config.expected_dataset("funding")


# ================================================================ P7/P8 economics unchanged


def test_p7_fixture_rows_and_coverage_equal_the_legacy_written_rows(
    fixture_dir, canonical_fixture_dir
):
    for db in ("contract.db", "index.db"):
        rows = []
        for directory in (fixture_dir, canonical_fixture_dir):
            con = sqlite3.connect(f"file:{directory / db}?mode=ro", uri=True)
            rows.append((con.execute("SELECT * FROM historical_candles ORDER BY 1,2,3,4,5").fetchall(),
                         con.execute("SELECT * FROM candle_coverage ORDER BY 1,2,3,4,5").fetchall()))  # fmt: skip
            con.close()
        assert rows[0] == rows[1]
        assert len(rows[0][0]) == (24 if db == "contract.db" else 23)


def test_p7_offline_smoke_results_are_identical_under_either_label(
    tmp_path, fixture_dir, canonical_fixture_dir
):
    reports = []
    for name, directory, sources in (("syn", fixture_dir, FIXTURE_SOURCES),
                                     ("can", canonical_fixture_dir, None)):  # fmt: skip
        path = _config(directory, tmp_path, sources=sources, name=f"{name}.json")
        out = tmp_path / f"out-{name}"
        assert cli.main(["funding-research", "--config", str(path), "--output", str(out)]) == 0
        reports.append(json.loads((out / "report.json").read_text(encoding="utf-8")))
    syn, can = (r["deterministic"] for r in reports)
    assert syn["results"] == can["results"]
    assert [c["status"] for c in syn["checks"]] == [c["status"] for c in can["checks"]]


def test_p8_multileg_offline_economics_do_not_depend_on_the_perpetual_label(monkeypatch):
    synthetic = {s.name: multileg_offline.result_view(multileg_offline.run_scenario(s))
                 for s in multileg_offline.SCENARIOS}  # fmt: skip
    old = dataclasses.replace(multileg_offline._PERP_DS, source=FAPI_KLINES)
    monkeypatch.setattr(multileg_offline, "_PERP_DS", old)
    legacy = {s.name: multileg_offline.result_view(multileg_offline.run_scenario(s))
              for s in multileg_offline.SCENARIOS}  # fmt: skip
    assert synthetic == legacy
    finals = {name: str(view["final_equity"]) for name, view in synthetic.items()}
    assert finals == {
        "closed_no_funding": "402",
        "close_instant_funding": "402.0101",
        "proportional_costs_and_funding": "401.7076",
        "open_at_end": "402",
    }


# ================================================================ P9 determinism + digests


def test_p9_config_without_sources_keeps_its_pre_19_14_digest():
    config = fixture_config()
    config.pop("sources")
    assert sha256_hex(canonical_json(config)) == PRE_19_14_FIXTURE_CONFIG_SHA256
    assert sha256_hex(canonical_json(fixture_config())) != PRE_19_14_FIXTURE_CONFIG_SHA256


def test_p9_offline_smoke_is_deterministic_and_records_the_synthetic_labels(tmp_path):
    reports = []
    for name in ("a", "b"):
        assert cli.main(["offline-smoke", "--output", str(tmp_path / name)]) == 0
        reports.append(json.loads((tmp_path / name / "report.json").read_text(encoding="utf-8")))
    a, b = reports
    assert a["deterministic_sha256"] == b["deterministic_sha256"]
    assert a["deterministic"]["run_input_sha256"] == b["deterministic"]["run_input_sha256"]
    det = a["deterministic"]
    assert det["config"]["sources"] == FIXTURE_SOURCES
    assert [i["registered_dataset"]["source"] for i in det["inputs"][:2]] == [
        FIXTURE_CONTRACT_SOURCE,
        FIXTURE_INDEX_SOURCE,
    ]
    assert "binance:GET" not in json.dumps(det)


def test_p9_the_source_label_is_part_of_the_input_fingerprints(
    tmp_path, fixture_dir, canonical_fixture_dir
):
    reports = []
    for name, directory, sources in (("syn", fixture_dir, FIXTURE_SOURCES),
                                     ("can", canonical_fixture_dir, None)):  # fmt: skip
        path = _config(directory, tmp_path, sources=sources, name=f"{name}.json")
        out = tmp_path / f"out-{name}"
        assert cli.main(["inspect", "--config", str(path), "--output", str(out)]) == 0
        reports.append(
            json.loads((out / "report.json").read_text(encoding="utf-8"))["deterministic"]
        )
    syn, can = reports
    for i in (0, 1):
        assert (
            syn["inputs"][i]["logical_fingerprint_sha256"]
            != can["inputs"][i]["logical_fingerprint_sha256"]
        )
    assert syn["run_input_sha256"] != can["run_input_sha256"]


# ================================================================ P10 isolation


def test_p10_offline_smoke_needs_no_network_keeps_context_and_never_overwrites(
    tmp_path, monkeypatch
):
    def no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    before = decimal.getcontext().copy()
    out = tmp_path / "smoke"
    assert cli.main(["offline-smoke", "--output", str(out)]) == 0
    first = (out / "report.json").read_bytes()
    assert cli.main(["offline-smoke", "--output", str(out)]) == 2
    assert (out / "report.json").read_bytes() == first
    after = decimal.getcontext()
    assert (after.prec, after.rounding, after.Emin, after.Emax) == (
        before.prec, before.rounding, before.Emin, before.Emax,
    )  # fmt: skip
