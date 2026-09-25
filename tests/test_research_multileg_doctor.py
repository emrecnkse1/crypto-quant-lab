"""Optional multileg-doctor + config reconciliation fixes (FUNDING_RESEARCH_SPEC.md Bölüm 19.16, R1–R12).

Reuses the config-test fixture helpers (real writers, N-fixture values from
the replay acceptance file). Economic expectations are hand-derived literals.
"""

import decimal
import json
import os
import socket
import subprocess
import sys
import urllib.request
from decimal import ROUND_DOWN, localcontext
from pathlib import Path

import pytest
import test_backtest_multileg_replay as acceptance
import test_research_multileg_config as cfg

from crypto_quant_lab.backtest import multileg as accounting
from crypto_quant_lab.backtest import multileg_replay as core
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.research import cli, multileg_store
from crypto_quant_lab.research.multileg_config import load_multileg_config
from crypto_quant_lab.research.report import canonical_json, sha256_hex
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

SPOT_SRC, PERP_SRC = cfg.SPOT_SRC, cfg.PERP_SRC
base_config, with_funding, intent = cfg.base_config, cfg.with_funding, cfg.intent
write_config, files, checks, inputs = cfg.write_config, cfg.files, cfg.checks, cfg.inputs
REPLAY_ONLY_RESULT_KEYS = {"final_equity", "fills", "funding_records", "equity_timeline_pre_fill",
                           "realized_pnl", "unrealized_pnl_at_end", "wallets", "total_pnl"}  # fmt: skip


@pytest.fixture
def fx(tmp_path):
    return cfg.build_fixture(tmp_path / "fx")


def run(command, config_path, output):
    code = cli.main([command, "--config", str(config_path), "--output", str(output)])
    return code, json.loads((output / "report.json").read_text(encoding="utf-8"))


def doctor(directory, config, output, name="config.json"):
    return run("multileg-doctor", write_config(directory, config, name), output)


def replay(directory, config, output, name="config.json"):
    return run("multileg-replay", write_config(directory, config, name), output)


def result_of(report):
    return report["deterministic"]["results"]


# ================================================================ R1 / B: reconciliation fixes


def test_r1_composite_spread_and_slippage_models_through_the_config(tmp_path, fx):
    composite = {"model": "composite", "components": [
        {"model": "proportional_commission", "rate": "0.001"},
        {"model": "proportional_spread", "half_spread_rate": "0.0005"},
        {"model": "proportional_slippage", "rate": "0.0002"},
    ]}  # fmt: skip
    code, report = replay(fx, base_config(costs={"spot": composite, "perpetual": cfg.ZERO}),
                          tmp_path / "a")  # fmt: skip
    # N1 402; spot 0.0017 x 100 = 0.17 at the open, 0.0017 x 101 = 0.1717 at the close
    assert (code, result_of(report)["final_equity"]) == (0, "401.6583")
    reversed_composite = composite | {"components": composite["components"][::-1]}
    code, reordered = replay(
        fx, base_config(costs={"spot": reversed_composite, "perpetual": cfg.ZERO}), tmp_path / "b",
        "reordered.json",
    )  # fmt: skip
    assert result_of(reordered)["final_equity"] == "401.6583"  # same sum here...
    assert (inputs(reordered)["replay_input"]["logical_fingerprint_sha256"]  # ...but the order
            != inputs(report)["replay_input"]["logical_fingerprint_sha256"])  # is never sorted  # fmt: skip
    spread = {"model": "proportional_spread", "half_spread_rate": "0.0005"}
    code, report = replay(fx, base_config(costs={"spot": cfg.ZERO, "perpetual": spread}),
                          tmp_path / "c", "spread.json")  # fmt: skip
    # perpetual 0.0005 x 102 = 0.051 and 0.0005 x 101 = 0.0505 -> 402 - 0.1015
    assert (code, result_of(report)["final_equity"]) == (0, "401.8985")


def test_r1_empty_decimal_string_is_refused(fx):
    config = base_config(wallets={"spot_cash": "", "perpetual_collateral": "200"})
    with pytest.raises(cli.ConfigError, match="wallets.spot_cash is not a decimal"):
        load_multileg_config(write_config(fx, config))


def test_r1_config_sha256_is_the_parsed_canonical_hash_not_a_byte_hash(tmp_path, fx):
    config = base_config()
    compact = write_config(fx, config, "compact.json")
    spaced = fx / "spaced.json"
    reordered = dict(reversed(list(config.items())))
    spaced.write_text("\ufeff" + json.dumps(reordered, indent=4), encoding="utf-8")
    assert compact.read_bytes() != spaced.read_bytes()
    assert load_multileg_config(compact).sha256 == load_multileg_config(spaced).sha256
    assert load_multileg_config(compact).sha256 == sha256_hex(canonical_json(config))


@pytest.mark.parametrize("command", ["multileg-replay", "multileg-doctor"])
def test_r1_output_never_targets_the_config_or_an_input(tmp_path, fx, command):
    config_path = write_config(fx, base_config())
    os.link(fx / "spot.db", fx / "spot-alias.db")
    before = files(fx)
    for target in (config_path, fx / "spot.db", fx / "spot-alias.db", fx):
        argv = [command, "--config", str(config_path), "--output", str(target)]
        assert cli.main(argv) == 2, target
    assert files(fx) == before


def test_r1_markdown_shows_inputs_settings_and_identity_meanings(tmp_path, fx):
    replay(fx, with_funding("f_t3.db", costs=cfg.PROPORTIONAL), tmp_path / "out")
    text = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    for fragment in (SPOT_SRC, PERP_SRC, '"coverage"', '"quality_status": "PASS"',
                     '"source_recorded": false', '"proportional_commission"', '"rate": "0.0005"',
                     '"decimal_context"', '"ROUND_HALF_EVEN"', '"multileg_input_sha256"',
                     "NOT the run identity"):  # fmt: skip
        assert fragment in text, fragment


def test_r1_multileg_input_identity_carries_provenance_that_run_input_does_not(tmp_path, fx):
    config = with_funding("f_t3.db")
    _, before = replay(fx, config, tmp_path / "a")
    cfg.write_candles(fx / "spot.db", "spot", "spot_trade", SPOT_SRC, [("101", "101")],
                      hours=[4], cover=(cfg.T4, cfg.T4 + cfg.H))  # fmt: skip  # outside the window
    _, outside = replay(fx, config, tmp_path / "b")
    for key in ("multileg_input_sha256", "input_evidence"):  # window-scoped evidence only
        assert result_of(outside)[key] == result_of(before)[key], key
    split = tmp_path / "split"  # same candles, coverage registered as two intervals
    cfg.build_fixture(split)
    (split / "spot.db").unlink()
    cfg.write_candles(split / "spot.db", "spot", "spot_trade", SPOT_SRC, acceptance.N_SPOT[:2],
                      cover=(cfg.T0, cfg.T0 + 2 * cfg.H))  # fmt: skip
    cfg.write_candles(split / "spot.db", "spot", "spot_trade", SPOT_SRC, acceptance.N_SPOT[2:],
                      hours=[2, 3], cover=(cfg.T0 + 2 * cfg.H, cfg.T4))  # fmt: skip
    _, other = replay(split, config, tmp_path / "c")
    assert (inputs(other)["replay_input"]["logical_fingerprint_sha256"]
            == inputs(before)["replay_input"]["logical_fingerprint_sha256"])  # fmt: skip
    assert other["deterministic"]["run_input_sha256"] == before["deterministic"]["run_input_sha256"]
    assert result_of(other)["multileg_input_sha256"] != result_of(before)["multileg_input_sha256"]
    assert len(result_of(other)["input_evidence"]["spot"]["coverage"]) == 2
    assert result_of(other)["final_equity"] == result_of(before)["final_equity"] == "402.0101"


# ================================================================ R2 doctor never replays


@pytest.fixture
def forbidden(monkeypatch):
    calls = []

    def sentinel(name):
        def refuse(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not run in multileg-doctor")

        return refuse

    for module, names in (
        (multileg_store, ("run_multileg_replay", "run_store_backed_multileg_replay")),
        (core, ("run_multileg_replay", "new_hedged_portfolio", "apply_hedged_open",
                "apply_hedged_close", "apply_perpetual_funding", "mark_hedged_portfolio")),
        (accounting, ("new_hedged_portfolio", "apply_hedged_open", "apply_hedged_close",
                      "apply_perpetual_funding", "mark_hedged_portfolio")),
    ):  # fmt: skip
        for name in names:
            monkeypatch.setattr(module, name, sentinel(f"{module.__name__}.{name}"))
    return calls


def test_r2_doctor_reads_the_real_stores_and_never_calls_replay_or_accounting(
    tmp_path, fx, forbidden, monkeypatch
):
    opened = []
    real_candle = SQLiteHistoricalCandleStore.open_read_only.__func__
    real_funding = SQLiteHistoricalFundingStore.open_read_only.__func__
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "open_read_only", classmethod(
        lambda cls, path: opened.append(Path(path).name) or real_candle(cls, path)))  # fmt: skip
    monkeypatch.setattr(SQLiteHistoricalFundingStore, "open_read_only", classmethod(
        lambda cls, path: opened.append(Path(path).name) or real_funding(cls, path)))  # fmt: skip
    code, report = doctor(fx, with_funding("f_t3.db", costs=cfg.PROPORTIONAL), tmp_path / "doc")
    assert (code, report["status"], report["warning_count"]) == (0, "succeeded", 0)
    assert forbidden == []
    assert opened == ["spot.db", "perp.db", "f_t3.db"]
    results = result_of(report)
    assert results["replay_executed"] is False
    assert not REPLAY_ONLY_RESULT_KEYS & set(results)
    assert checks(report) == {
        "config.parse": "passed",
        "inputs.provenance_and_coverage": "passed",
        "identity.replay_input": "passed",
        "economics.solvency": "skipped",
        "replay.core_validation": "skipped",
        "economics.results": "skipped",
    }
    skipped = [c["detail"] for c in report["deterministic"]["checks"] if c["status"] == "skipped"]
    assert all(detail.startswith("NOT_EVALUATED: ") for detail in skipped)
    evidence = results["input_evidence"]
    assert evidence["declared_sources"] == {"spot": SPOT_SRC, "perpetual": PERP_SRC}
    assert evidence["spot"]["registered_dataset"]["source"] == SPOT_SRC
    assert (evidence["spot"]["candle_count"], evidence["funding"]["event_count"]) == (4, 1)
    assert evidence["funding"]["source_recorded"] is False


def test_r2_doctor_and_replay_share_the_input_identities_not_the_output(tmp_path, fx):
    config = with_funding("f_t3.db", costs=cfg.PROPORTIONAL)
    _, doc = doctor(fx, config, tmp_path / "doc")
    _, rep = replay(fx, config, tmp_path / "rep")
    assert (
        result_of(doc)["replay_input_sha256"]
        == (inputs(rep)["replay_input"]["logical_fingerprint_sha256"])
    )
    assert result_of(doc)["multileg_input_sha256"] == result_of(rep)["multileg_input_sha256"]
    assert doc["deterministic_sha256"] != rep["deterministic_sha256"]
    assert result_of(rep)["final_equity"] == "401.7076"


# ================================================================ R3 same shared-stage rejections


def _bad_fixtures(tmp_path, fx):
    other = tmp_path / "index-perp"
    other.mkdir()
    cfg.write_candles(other / "perp.db", "usdm_perpetual", "index_price", PERP_SRC,
                      acceptance.N_PERP)  # fmt: skip
    holes = tmp_path / "holes"
    holes.mkdir()
    cfg.write_candles(holes / "spot.db", "spot", "spot_trade", SPOT_SRC,
                      [acceptance.N_SPOT[k] for k in (0, 1, 3)], hours=[0, 1, 3])  # fmt: skip
    for directory, names in ((other, ("spot.db", "f_none.db")), (holes, ("perp.db", "f_none.db"))):
        for name in names:
            (directory / name).write_bytes((fx / name).read_bytes())
    cfg.write_funding(fx / "f_uncollected.db", [], cover=None)
    cfg.write_funding(fx / "f_eth.db", [], symbol="ETHUSDT")
    return [
        (fx, base_config(sources={"spot": SPOT_SRC, "perpetual": "x:y"}), "[provenance_mismatch]"),
        (other, base_config(), "price_kind='index_price'"),
        (fx, base_config(run_end="2026-01-01T05:00:00Z", as_of="2026-01-01T05:00:00Z"),
         "[incomplete_coverage] spot"),
        (holes, base_config(), "[missing_data] spot has 3 of 4"),
        (fx, with_funding("f_uncollected.db"), "[incomplete_coverage] funding"),
        (fx, with_funding("f_eth.db"), "[incomplete_coverage] funding"),
        (fx, with_funding("absent.db"), "stores.funding: file not found: absent.db"),
        (fx, base_config(costs={"spot": {"model": "fixed"}, "perpetual": cfg.ZERO}),
         "unsupported cost model 'fixed'"),
    ]  # fmt: skip


def test_r3_doctor_and_replay_reject_bad_inputs_identically(tmp_path, fx):
    for index, (directory, config, fragment) in enumerate(_bad_fixtures(tmp_path, fx)):
        name = f"bad{index}.json"
        d_code, d_report = doctor(directory, config, tmp_path / f"d{index}", name)
        r_code, r_report = replay(directory, config, tmp_path / f"r{index}", name)
        assert (d_code, r_code) == (1, 1), fragment
        assert d_report["deterministic"]["errors"] == r_report["deterministic"]["errors"]
        assert fragment in d_report["deterministic"]["errors"][0]
        assert d_report["deterministic"]["results"] is None
    assert not (fx / "absent.db").exists()


def test_r3_verified_empty_funding_passes_both(tmp_path, fx):
    assert doctor(fx, with_funding("f_none.db"), tmp_path / "d")[0] == 0
    code, report = replay(fx, with_funding("f_none.db"), tmp_path / "r")
    assert (code, result_of(report)["funding_paid"]) == (0, "0")


# ================================================================ R4 doctor does not judge solvency


def test_r4_doctor_pass_is_not_a_solvency_or_fill_guarantee(tmp_path, fx):
    too_big = base_config(intents=[intent("OPEN", 1, "3"), intent("CLOSE", 3, "3")])
    d_code, d_report = doctor(fx, too_big, tmp_path / "doc")
    assert (d_code, d_report["status"]) == (0, "succeeded")
    assert checks(d_report)["economics.solvency"] == "skipped"
    assert any(
        "replay will succeed" in item for item in d_report["deterministic"]["does_not_prove"]
    )
    r_code, r_report = replay(fx, too_big, tmp_path / "rep")
    # 3 x 100 = 300 > 200 spot cash
    assert (r_code, r_report["status"]) == (1, "failed")
    assert r_report["deterministic"]["errors"][0].startswith("ValueError: insufficient spot cash")


# ================================================================ R5 replay needs no doctor; one read


def test_r5_replay_runs_without_any_doctor_and_reads_each_store_once(tmp_path, fx, monkeypatch):
    queries, opened = [], []
    real_query = SQLiteHistoricalCandleStore.query
    real_open = SQLiteHistoricalCandleStore.open_read_only.__func__
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "query",
                        lambda self, *a: queries.append(a[1]) or real_query(self, *a))  # fmt: skip
    monkeypatch.setattr(SQLiteHistoricalCandleStore, "open_read_only", classmethod(
        lambda cls, path: opened.append(Path(path).name) or real_open(cls, path)))  # fmt: skip
    code, report = replay(fx, with_funding("f_t3.db"), tmp_path / "rep")
    assert (code, result_of(report)["final_equity"]) == (0, "402.0101")  # N2
    assert (queries, opened) == (["spot", "usdm_perpetual"], ["spot.db", "perp.db"])
    assert not list(tmp_path.glob("**/doctor*"))


# ================================================================ R6 a doctor PASS is not a ticket


def test_r6_replay_revalidates_inputs_changed_after_a_doctor_pass(tmp_path, fx):
    config = with_funding("f_t3.db")
    code, doc = doctor(fx, config, tmp_path / "doc")
    assert code == 0
    rows = list(acceptance.N_SPOT)
    rows[3] = ("101", "103")
    changed = cfg.build_fixture(tmp_path / "changed", spot_rows=rows)
    os.replace(changed / "spot.db", fx / "spot.db")  # the task fixture changes after the doctor
    code, rep = replay(fx, config, tmp_path / "rep")
    assert code == 0
    assert (inputs(rep)["replay_input"]["logical_fingerprint_sha256"]
            != result_of(doc)["replay_input_sha256"])  # fmt: skip
    relabeled = cfg.build_fixture(tmp_path / "relabeled", perp_src="synthetic:other/perp")
    os.replace(relabeled / "perp.db", fx / "perp.db")
    code, rep = replay(fx, config, tmp_path / "rep2")
    assert code == 1
    assert "[provenance_mismatch] perpetual" in rep["deterministic"]["errors"][0]


# ================================================================ R7 / R8 read-only, paths, isolation


def test_r7_doctor_leaves_sources_unchanged_and_releases_connections(tmp_path, fx):
    write_config(fx, with_funding("f_t3.db"))
    write_config(fx, base_config(sources={"spot": SPOT_SRC, "perpetual": "x:y"}), "bad.json")
    before = files(fx)
    assert run("multileg-doctor", fx / "config.json", tmp_path / "ok")[0] == 0
    assert run("multileg-doctor", fx / "bad.json", tmp_path / "bad")[0] == 1
    assert files(fx) == before  # bytes, mtimes, no -wal/-shm/-journal
    for name in ("spot.db", "perp.db", "f_t3.db", "f_none.db"):
        os.replace(fx / name, fx / f"moved-{name}")


def test_r8_paths_aliases_context_and_network(tmp_path, fx, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    config_path = write_config(fx, with_funding("f_t3.db", costs=cfg.PROPORTIONAL))
    _, reference = run("multileg-doctor", config_path, tmp_path / "ref")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        code, again = run("multileg-doctor", Path(os.path.relpath(config_path)), tmp_path / "b")
        alias = base_config(stores={"spot": "spot.db", "perpetual": "./spot.db",
                                    "funding": "f_none.db"})  # fmt: skip
        a_code, a_report = run("multileg-doctor", write_config(fx, alias, "alias.json"),
                               tmp_path / "alias")  # fmt: skip
        assert (decimal.getcontext().prec, decimal.getcontext().rounding) == (3, ROUND_DOWN)
    assert code == 0 and again["deterministic_sha256"] == reference["deterministic_sha256"]
    assert (
        a_code == 1 and "two roles point to the same file" in a_report["deterministic"]["errors"][0]
    )
    assert not list(elsewhere.iterdir())


# ================================================================ R9 determinism and sensitivity


def test_r9_same_frozen_inputs_same_doctor_result_and_content_changes_are_seen(tmp_path, fx):
    config = with_funding("f_t3.db")
    _, a = doctor(fx, config, tmp_path / "a")
    _, b = doctor(fx, config, tmp_path / "deeper-b")
    assert a["deterministic_sha256"] == b["deterministic_sha256"]
    cfg.write_funding(fx / "f_t3_other.db", [cfg.event(acceptance.T3, "0.0002")])
    _, c = doctor(fx, with_funding("f_t3_other.db"), tmp_path / "c", "other.json")
    for key in ("replay_input_sha256", "multileg_input_sha256"):
        assert result_of(c)[key] != result_of(a)[key], key


# ================================================================ R12 imports and injected identity failures


def test_r12_imports_have_no_side_effects(tmp_path):
    code = (
        "import socket, sqlite3, urllib.request\n"
        "def refuse(*a, **k): raise SystemExit('side effect during import')\n"
        "socket.create_connection = sqlite3.connect = urllib.request.urlopen = refuse\n"
        "import crypto_quant_lab.research.multileg_store, crypto_quant_lab.research.multileg_config\n"
        "print('imported')\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                               text=True, check=False)  # fmt: skip
    assert (completed.returncode, completed.stdout.strip()) == (0, "imported")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["multileg-doctor", "multileg-replay"])
def test_r12_missing_or_failing_identity_is_never_a_success(tmp_path, fx, monkeypatch, command):
    monkeypatch.setattr(multileg_store, "describe_cost_model", lambda model: None)
    code, report = run(command, write_config(fx, base_config()), tmp_path / "none")
    assert (code, report["status"]) == (1, "failed")
    assert checks(report)["identity.replay_input"] == "failed"
    assert result_of(report)["multileg_input_sha256"] is None

    def broken(*args, **kwargs):
        raise RuntimeError("fingerprint unavailable")

    monkeypatch.setattr(multileg_store, "replay_input_fingerprint", broken)
    code, report = run(command, fx / "config.json", tmp_path / "err")
    assert (code, report["deterministic"]["results"]) == (1, None)
    assert report["deterministic"]["errors"] == ["RuntimeError: fingerprint unavailable"]


# ================================================================ E: interrupted example


def test_e_failed_example_is_reported_and_never_announced(tmp_path, monkeypatch, capsys):
    def fail(self, *args, **kwargs):
        raise RuntimeError("disk full (injected)")

    monkeypatch.setattr(SQLiteHistoricalFundingStore, "write_ingestion_batch", fail)
    target = tmp_path / "example"
    assert cli.main(["multileg-example", "--output", str(target)]) == 1
    out, err = capsys.readouterr()
    assert "example written" not in out
    assert "example NOT written (RuntimeError: disk full (injected))" in err
    assert not (target / "config.json").exists()
    assert not list(target.glob(".*.tmp"))
    os.replace(target, tmp_path / "moved")  # every writer was closed
    assert cli.main(["multileg-example", "--output", str(tmp_path / "moved")]) == 2


def test_e_file_not_found_while_writing_is_exit_1_not_an_output_error(
    tmp_path, monkeypatch, capsys
):
    target = tmp_path / "example"

    def missing(self, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(target / "funding_none.db"))

    monkeypatch.setattr(SQLiteHistoricalFundingStore, "write_ingestion_batch", missing)
    assert cli.main(["multileg-example", "--output", str(target)]) == 1
    out, err = capsys.readouterr()
    assert "example written" not in out
    assert "example NOT written (FileNotFoundError:" in err
    assert "the new directory example is incomplete, has no config and is left as is" in err
    assert target.is_dir() and not (target / "config.json").exists()
    assert not list(target.glob(".*.tmp"))
    os.replace(target, tmp_path / "moved")  # writers closed; nothing deleted or repaired
    assert (tmp_path / "moved" / "spot.db").is_file()


def test_e_output_path_errors_keep_exit_2_and_touch_nothing(tmp_path, capsys):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "user.txt").write_text("keep", encoding="utf-8")
    before = files(existing)
    assert cli.main(["multileg-example", "--output", str(existing)]) == 2
    assert files(existing) == before
    missing_parent = tmp_path / "no-parent" / "example"
    assert cli.main(["multileg-example", "--output", str(missing_parent)]) == 2
    assert not (tmp_path / "no-parent").exists()
    err = capsys.readouterr().err
    assert err.count("error: ") == 2  # one plain output error per call
    assert "example NOT written" not in err
