"""Synthetic multi-leg offline demo: end-to-end, determinism, no network, safe output."""

import json
import socket
from dataclasses import replace
from decimal import Decimal

import pytest

from crypto_quant_lab.research import multileg_offline as demo


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the offline demo must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _report(path):
    return json.loads((path / "report.json").read_text(encoding="utf-8"))


def test_demo_runs_end_to_end_with_hand_derived_results(tmp_path, no_network, capsys):
    assert demo.main(["--output", str(tmp_path / "a")]) == 0
    report = _report(tmp_path / "a")
    assert (report["status"], report["warning_count"], report["schema_version"]) == (
        "succeeded",
        0,
        "crypto-quant-lab/research-report/v2",
    )
    results = report["deterministic"]["results"]
    costs = results["proportional_costs_and_funding"]
    # spot 200 - 100 - 0.100 + 101 - 0.101; collateral 200 - 0.0510 + 0.0101 + 1 - 0.0505
    assert Decimal(costs["wallets"]["spot_cash"]) == Decimal("200.799")
    assert Decimal(costs["wallets"]["perpetual_collateral"]) == Decimal("200.9086")
    assert Decimal(costs["final_equity"]) == Decimal("401.7076")
    assert [r["signed_cost"] for r in costs["funding_records"]] == ["-0.0101"]
    opened = results["open_at_end"]
    assert (opened["lifecycle_at_end"], opened["position_open_at_end"]) == ("HEDGED_OPEN", True)
    assert Decimal(opened["unrealized_pnl_at_end"]["perpetual"]) == Decimal(1)
    assert Decimal(opened["unrealized_pnl_at_end"]["spot"]) == Decimal(1)
    assert len(opened["fills"]) == 1
    assert all(c["status"] == "passed" for c in report["deterministic"]["checks"])
    assert any("NOT A STRATEGY" in item for item in report["deterministic"]["limitations"])
    assert "succeeded" in capsys.readouterr().out


def test_two_clean_runs_are_identical_and_existing_output_is_kept(tmp_path, no_network):
    assert demo.main(["--output", str(tmp_path / "a")]) == 0
    assert demo.main(["--output", str(tmp_path / "b")]) == 0
    a, b = _report(tmp_path / "a"), _report(tmp_path / "b")
    assert a["deterministic_sha256"] == b["deterministic_sha256"]
    assert a["deterministic"]["run_input_sha256"] == b["deterministic"]["run_input_sha256"]
    before = (tmp_path / "a" / "report.json").read_bytes()
    assert demo.main(["--output", str(tmp_path / "a")]) == 2
    assert (tmp_path / "a" / "report.json").read_bytes() == before
    assert str(tmp_path) not in json.dumps(a["deterministic"])


def test_invalid_scenario_produces_a_failed_bundle_not_a_success(tmp_path, no_network):
    broken = replace(demo.SCENARIOS[0], name="unaffordable", spot_fee_rate="3")  # fee 300 > cash
    _path, report = demo.run_offline_demo(tmp_path / "f", (broken,))
    assert report["status"] == "failed"
    assert "insufficient spot cash" in report["deterministic"]["errors"][0]
    assert report["deterministic"]["results"] == {}
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]  # no staging leftovers
    wrong = replace(demo.SCENARIOS[0], name="wrong_expectation", expected_final_equity="999")
    _, report = demo.run_offline_demo(tmp_path / "g", (wrong,))
    assert report["status"] == "failed"
    assert report["deterministic"]["checks"][0]["status"] == "failed"


def test_input_changes_change_the_input_fingerprint(tmp_path, no_network):
    base = demo.build_deterministic()
    cheaper = demo.build_deterministic(
        (replace(demo.SCENARIOS[2], spot_fee_rate="0.0009"), *demo.SCENARIOS[:2])
    )
    assert base["run_input_sha256"] != cheaper["run_input_sha256"]
    assert base["run_input_sha256"] == demo.build_deterministic()["run_input_sha256"]
