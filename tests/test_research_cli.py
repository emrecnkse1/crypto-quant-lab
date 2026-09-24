"""Research operations: report contract, CLI commands, offline fixture, diagnostics, doctor.

No network: public-smoke is only exercised for its opt-in refusal. Expected
economic values are hand-derived in research/offline_fixture.py's docstring
and repeated here as literals.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_quant_lab.backtest.costs import ProportionalCommissionModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.policy import PolicyContext
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import cli
from crypto_quant_lab.research.diagnostics import diagnose_funding_research_trial
from crypto_quant_lab.research.funding_carry import (
    FundingCarryPolicy,
    decide_funding_carry,
    funding_carry_candidate,
    load_funding_signal_history,
)
from crypto_quant_lab.research.offline_fixture import (
    FIXTURE_START,
    FIXTURE_SYMBOL,
    build_offline_fixture,
    fixture_config,
)
from crypto_quant_lab.research.report import (
    REPORT_SCHEMA_VERSION,
    OutputBundle,
    build_report,
    canonical_json,
    check,
    fingerprint,
    to_jsonable,
)
from crypto_quant_lab.research.usdm_perpetual import evaluate_usdm_perpetual_funding_research
from crypto_quant_lab.storage.base import StorageError
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.windows import TemporalWindow

HOUR = timedelta(hours=1)
T0 = FIXTURE_START


def _payload(**overrides):
    base = {"checks": [], "errors": [], "limitations": [], "does_not_prove": [], "results": {}}
    return {**base, **overrides}


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    return build_offline_fixture(tmp_path_factory.mktemp("fx") / "fixture").directory


def _write_config(directory: Path, config: dict, name="config.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _fixture_config_path(fixture_dir, tmp_path, **changes):
    config = fixture_config()
    config["stores"] = {role: str(fixture_dir / name) for role, name in config["stores"].items()}
    config.update(changes)
    return _write_config(tmp_path, config)


# ================================================================ report contract


def test_to_jsonable_rules():
    aware = datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))
    assert to_jsonable({"d": Decimal("0.00010"), "t": aware, "l": (1, "x", None, True)}) == {
        "d": "0.00010",
        "t": "2026-01-01T00:00:00+00:00",
        "l": [1, "x", None, True],
    }
    for bad, error in (
        (0.1, TypeError),
        (Decimal("NaN"), ValueError),
        (datetime(2026, 1, 1), ValueError),  # noqa: DTZ001 - naive on purpose
        ({1: "x"}, TypeError),
        (HOUR, TypeError),
    ):
        with pytest.raises(error):
            to_jsonable(bad)
    assert canonical_json({"b": 1, "a": [Decimal(2)]}) == '{"a":["2"],"b":1}'
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    with pytest.raises(ValueError, match="check status"):
        check("x", "ok", "detail")


def test_deterministic_hash_ignores_wall_clock_and_status_reflects_failures():
    one = build_report(run_kind="k", deterministic=_payload(), created_at=T0)
    two = build_report(run_kind="k", deterministic=_payload(), created_at=T0 + HOUR)
    assert one["deterministic_sha256"] == two["deterministic_sha256"]
    assert one["run_metadata"]["created_at"] != two["run_metadata"]["created_at"]
    assert one["schema_version"] == REPORT_SCHEMA_VERSION
    assert one["status"] == "succeeded"
    failed_check = _payload(checks=[check("c", "failed", "d")])
    assert build_report(run_kind="k", deterministic=failed_check, created_at=T0)["status"] == (
        "failed"
    )
    errored = _payload(errors=["boom"])
    assert build_report(run_kind="k", deterministic=errored, created_at=T0)["status"] == "failed"
    with pytest.raises(ValueError, match="must contain 'errors'"):
        build_report(run_kind="k", deterministic={"checks": []}, created_at=T0)


def test_output_bundle_is_staged_atomic_and_never_overwrites(tmp_path):
    target = tmp_path / "out"
    bundle = OutputBundle(target)
    bundle.write_text("a.txt", "x")
    assert not target.exists()
    assert [p.name for p in bundle.staging.iterdir()] == ["a.txt"]  # no temp leftovers
    assert bundle.commit() == target
    assert (target / "a.txt").read_text(encoding="utf-8") == "x"
    with pytest.raises(FileExistsError):
        OutputBundle(target)
    with pytest.raises(FileNotFoundError):
        OutputBundle(tmp_path / "missing" / "out")


# ================================================================ config validation


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"start": "2026-01-05T00:00:00"}, "explicit offset"),
        ({"start": "2026-01-05T00:30:00Z"}, "aligned"),
        ({"as_of": "2026-01-05T12:00:00Z"}, "as_of must be >= end"),
        ({"timeframe": "15m"}, "timeframe"),
        ({"symbol": "btcusdt"}, "symbol"),
        ({"config_version": 2}, "config_version"),
        ({"stores": {"spot": "x.db"}}, "unknown role"),
        ({"stores": {"contract": "a.db", "index": "sub/../a.db"}}, "same file"),
    ],
)
def test_config_validation_names_the_field(tmp_path, change, message):
    config = {**fixture_config(), **change}
    with pytest.raises(cli.ConfigError, match=message):
        cli.load_config(_write_config(tmp_path, config))


def test_config_rejects_floats_and_bad_research_fields(tmp_path):
    path = tmp_path / "float.json"
    path.write_text(json.dumps(fixture_config()).replace('"0.0002"', "0.0002"), encoding="utf-8")
    with pytest.raises(cli.ConfigError, match="not allowed"):
        cli.load_config(path)
    for mutate, message in (
        (lambda fr: fr["windows"].append(["2026-01-04T00:00:00Z", "2026-01-05T00:00:00Z"]),
         "inside \\[start, end\\)"),
        (lambda fr: fr["candidate"].update(long_entry_rate="0.0003"), "below short_entry_rate"),
        (lambda fr: fr["cost"].update(commission_rate="-0.1"), "cost rates"),
        (lambda fr: fr.pop("publication_lag_seconds"), "publication_lag_seconds"),
    ):  # fmt: skip
        config = fixture_config()
        mutate(config["funding_research"])
        with pytest.raises(cli.ConfigError, match=message):
            cli.load_config(_write_config(tmp_path, config))


def test_effective_config_records_file_names_not_paths(fixture_dir, tmp_path):
    config = cli.load_config(_fixture_config_path(fixture_dir, tmp_path))
    assert config.effective()["stores"] == {
        "contract": "contract.db",
        "index": "index.db",
        "funding": "funding.db",
    }
    assert str(tmp_path) not in json.dumps(config.effective())


# ================================================================ commands


def test_offline_smoke_end_to_end_is_deterministic_and_refuses_overwrite(tmp_path, capsys):
    assert cli.main(["offline-smoke", "--output", str(tmp_path / "a")]) == 0
    assert cli.main(["offline-smoke", "--output", str(tmp_path / "b")]) == 0
    reports = [json.loads((tmp_path / d / "report.json").read_text()) for d in ("a", "b")]
    assert reports[0]["status"] == "succeeded"
    assert reports[0]["deterministic_sha256"] == reports[1]["deterministic_sha256"]
    checks = {c["name"]: c["status"] for c in reports[0]["deterministic"]["checks"]}
    for name in ("expectation.basis", "expectation.carry_s2bp_l-1bp",
                 "expectation.no_trade_control", "diagnostics.carry_s2bp_l-1bp"):  # fmt: skip
        assert checks[name] == "passed"
    carry = reports[0]["deterministic"]["results"]["funding_research"]["runs"]["carry_s2bp_l-1bp"]
    first = carry["windows"][0]
    assert (first["fill_count"], Decimal(first["final_equity"])) == (2, Decimal("999.805"))
    assert Decimal(first["total_cost_including_funding"]) == Decimal("0.195")  # 0.2 - 0.005
    assert first["diagnostics"]["reason_counts"] == {"neutral_band": 4, "short_threshold_met": 8}
    before = (tmp_path / "a" / "report.json").read_bytes()
    assert cli.main(["offline-smoke", "--output", str(tmp_path / "a")]) == 2
    assert (tmp_path / "a" / "report.json").read_bytes() == before
    assert "refusing to overwrite" in capsys.readouterr().err
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]  # no staging leftovers


def test_failed_run_produces_a_failed_bundle_not_a_quiet_success(tmp_path, fixture_dir):
    config = fixture_config()
    config["stores"] = {
        "contract": str(tmp_path / "nope.db"),
        "index": str(fixture_dir / "index.db"),
    }
    path = _write_config(tmp_path, config)
    assert cli.main(["basis-report", "--config", str(path), "--output", str(tmp_path / "o")]) == 1
    report = json.loads((tmp_path / "o" / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["deterministic"]["results"] is None
    assert "store file does not exist: nope.db" in report["deterministic"]["errors"][0]
    assert not (tmp_path / "nope.db").exists()  # never created


def test_inspect_basis_and_funding_commands_on_the_fixture(fixture_dir, tmp_path):
    before = {p.name: p.read_bytes() for p in fixture_dir.glob("*.db")}
    path = _fixture_config_path(fixture_dir, tmp_path)
    for command in ("inspect", "basis-report", "funding-research"):
        out = tmp_path / command
        assert cli.main([command, "--config", str(path), "--output", str(out)]) == 0
    inspect = json.loads((tmp_path / "inspect" / "report.json").read_text())["deterministic"]
    index_entry = next(i for i in inspect["inputs"] if i["role"] == "index")
    assert index_entry["missing_open_times"] == ["2026-01-05T09:00:00+00:00"]
    assert index_entry["candle_count"] == 23
    funding_entry = next(i for i in inspect["inputs"] if i["role"] == "funding")
    assert (funding_entry["event_count"], funding_entry["quality_status"]) == (4, "PASS")
    basis = json.loads((tmp_path / "basis-report" / "report.json").read_text())["deterministic"]
    first = basis["results"]["observations"][0]
    assert (first["close_basis"], first["close_basis_rate"], first["available_at"]) == (
        "0.5", "0.005", "2026-01-05T01:00:00+00:00",
    )  # fmt: skip
    # (0.5 - 1 + 0 + 20 * 0.1) / 23 = 1.5 / 23, reported with 34 significant digits
    assert basis["results"]["close_basis"]["mean"] == "0.06521739130434782608695652173913043"
    assert {p.name: p.read_bytes() for p in fixture_dir.glob("*.db")} == before


def test_public_smoke_requires_explicit_network_opt_in(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be touched")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    assert cli.main(["public-smoke", "--output", str(tmp_path / "p")]) == 2
    assert "--allow-network" in capsys.readouterr().err
    assert not (tmp_path / "p").exists()


# ================================================================ read-only guarantees / doctor


def _legacy_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE historical_candles (x TEXT)")
    connection.commit()
    connection.close()


def test_read_only_store_access_never_creates_or_migrates(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        cli.open_candle_store(missing)
    assert not missing.exists()
    legacy = tmp_path / "legacy.db"
    _legacy_db(legacy)
    before = legacy.read_bytes()
    with pytest.raises(StorageError, match="lacks tables"):
        cli.open_candle_store(legacy)
    assert legacy.read_bytes() == before
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not sqlite at all" * 100)
    with pytest.raises(StorageError, match="not a readable SQLite"):
        cli.sqlite_tables(garbage)


def test_doctor_reports_concrete_problems_without_writing(fixture_dir, tmp_path):
    legacy = tmp_path / "legacy.db"
    _legacy_db(legacy)
    legacy_bytes = legacy.read_bytes()
    config = fixture_config()
    config["stores"] = {
        "contract": str(fixture_dir / "contract.db"),
        "index": str(fixture_dir / "contract.db").replace("contract.db", "index.db"),
        "funding": str(legacy),
    }
    lines = cli.doctor(_write_config(tmp_path, config), tmp_path)
    statuses = {name: status for status, name, _ in lines}
    assert statuses["output"] == "FAIL"  # exists
    assert statuses["funding.schema"] == "FAIL"
    assert legacy.read_bytes() == legacy_bytes
    config["stores"] = {"contract": str(fixture_dir / "contract.db"),
                        "index": str(tmp_path / "absent.db")}  # fmt: skip
    lines = cli.doctor(_write_config(tmp_path, config, "c2.json"), None)
    assert ("FAIL", "index.store") in {(s, n) for s, n, _ in lines}
    assert not (tmp_path / "absent.db").exists()
    config["stores"] = {"contract": str(fixture_dir / "index.db"),
                        "index": str(fixture_dir / "contract.db")}  # swapped roles  # fmt: skip
    lines = cli.doctor(_write_config(tmp_path, config, "c3.json"), None)
    failed = {n for s, n, _ in lines if s == "FAIL"}
    assert {"contract.provenance", "index.provenance"} <= failed
    assert cli.main(["doctor", "--config", str(tmp_path / "c3.json")]) == 1


def test_doctor_fails_cleanly_on_an_invalid_config(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    lines = cli.doctor(path)
    assert lines[-1][0:2] == ("FAIL", "config")


# ================================================================ diagnostics (Package E)


def _trial(fixture_dir, *, windows, max_age=timedelta(hours=9), coverage_start=None):
    contract = SQLiteHistoricalCandleStore(fixture_dir / "contract.db")
    funding = SQLiteHistoricalFundingStore(fixture_dir / "funding.db")
    history = load_funding_signal_history(
        funding, exchange="binance", market_type="usdm_perpetual", symbol=FIXTURE_SYMBOL,
        coverage_start=coverage_start or T0 - HOUR * 8, coverage_end=T0 + HOUR * 24,
        publication_lag=timedelta(seconds=60),
    )  # fmt: skip
    candidate = funding_carry_candidate(
        "c", short_entry_rate=Decimal("0.0002"), long_entry_rate=Decimal("-0.0001"),
        max_funding_age=max_age, publication_lag=timedelta(seconds=60),
    )  # fmt: skip
    trial = evaluate_usdm_perpetual_funding_research(
        contract, funding, history, candidate,
        windows=tuple(TemporalWindow(start=T0 + HOUR * a, end=T0 + HOUR * b) for a, b in windows),
        timeframe="1h", as_of_time=T0 + HOUR * 48,
        config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
        cost_model=ProportionalCommissionModel(rate=Decimal("0.001")),
        funding_model=LinearFundingModel(),
    )  # fmt: skip
    return diagnose_funding_research_trial(contract, history, trial), history


def test_diagnostics_separate_signal_threshold_target_and_fill_counts(fixture_dir):
    (a, b), _ = _trial(fixture_dir, windows=((0, 12), (12, 24)))
    assert (a.decisions_evaluated, a.signal_visible, a.fresh_signal, a.threshold_met) == (
        12, 12, 12, 8,
    )  # fmt: skip
    assert (a.target_changes, a.executable_target_changes, a.engine_fill_count) == (2, 2, 2)
    assert a.consistent_with_engine and not a.final_decision_unexecuted
    assert (b.threshold_met, b.target_changes, b.engine_fill_count) == (0, 0, 0)
    assert b.reason_counts == (("neutral_band", 12),)


def test_diagnostics_report_stale_and_missing_signals(fixture_dir):
    (stale,), _ = _trial(fixture_dir, windows=((0, 12),), max_age=HOUR)
    # 1h: 0h event age 1h -> SHORT; 2h..8h and 10h..12h stale; 9h: 8h event -> neutral
    assert stale.reason_counts == (
        ("neutral_band", 1), ("short_threshold_met", 1), ("stale_signal", 10),
    )  # fmt: skip
    assert (stale.signal_visible, stale.fresh_signal, stale.engine_fill_count) == (12, 2, 2)
    (none,), _ = _trial(fixture_dir, windows=((0, 12),), coverage_start=T0 + HOUR / 2)
    # events at 8h/16h only; nothing is known before 8h + 60s
    assert none.reason_counts == (("neutral_band", 4), ("no_settled_signal", 8))
    assert (none.signal_visible, none.engine_fill_count) == (4, 0)


def test_diagnostics_flag_the_unexecutable_final_decision(fixture_dir):
    (window,), _ = _trial(fixture_dir, windows=((7, 9),))
    # 8h decision SHORT (filled at candle 8 open); 9h decision FLAT on the last candle, unfilled
    assert (window.target_changes, window.executable_target_changes) == (2, 1)
    assert window.final_decision_unexecuted
    assert (window.engine_fill_count, window.consistent_with_engine) == (1, True)


def test_policy_and_decision_rule_agree_everywhere(fixture_dir):
    _, history = _trial(fixture_dir, windows=((0, 12),))
    params = {"short_entry_rate": Decimal("0.0002"), "long_entry_rate": Decimal("-0.0001"),
              "max_funding_age": timedelta(hours=3)}  # fmt: skip
    policy = FundingCarryPolicy(history, **params)
    seen = set()
    for minutes in range(0, 24 * 60, 7):
        as_of = T0 + timedelta(minutes=minutes)
        candle = Candle(FIXTURE_SYMBOL, "1h", as_of - HOUR, Decimal(1), Decimal(1), Decimal(1),
                        Decimal(1), Decimal(0))  # fmt: skip
        decision = decide_funding_carry(history, as_of, **params)
        context = PolicyContext(as_of_time=as_of, candles=(candle,))
        assert policy.target_position(context) is decision.target
        seen.add(decision.reason)
    assert seen == {"short_threshold_met", "stale_signal", "neutral_band"}
    assert decide_funding_carry(history, T0 + HOUR, **params).target is PositionTarget.SHORT
