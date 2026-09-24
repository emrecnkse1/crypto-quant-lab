"""public-smoke full flow through a mocked HTTP transport (no network).

The fake server answers `urllib.request.urlopen` like Binance: klines with
open time in [startTime, endTime] inclusive, official basis records with
timestamp in [startTime, endTime] inclusive. Clock: 2026-03-10T05:00Z, so the
window is [2026-03-03T00:00Z, 2026-03-10T00:00Z) = 168 hourly slots.

Hand-derived baseline: contract open = close = 100.1, index open = close =
100, official record at T: futuresPrice 100.1, indexPrice 100, basis 0.1,
basisRate 0.0010. Close basis 0.1 / 100 = 0.001 = official 0.1 / 100, so no
rate difference; the official record at the window start has no observation
closing there and the observation closing at the window end has no official
record (endTime handled half-open): 167 comparable pairs.
"""

import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.research import cli
from crypto_quant_lab.research.public_smoke import public_smoke_builder

NOW = datetime(2026, 3, 10, 5, tzinfo=UTC)
START = datetime(2026, 3, 3, tzinfo=UTC)
START_MS = 1772496000000  # 2026-03-03T00:00:00Z
HOUR_MS = 3_600_000
SLOTS = 168


def _ts(hour):
    return (START + timedelta(hours=hour)).isoformat()


class FakeBinance:
    def __init__(self):
        self.calls = []
        self.contract_open = {}  # hour -> open override
        self.official = {}  # hour -> dict override
        self.drop_index = set()
        self.fail = {}  # (path, symbol) -> exception factory

    def urlopen(self, url, timeout):
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        symbol = query.get("symbol") or query.get("pair")
        self.calls.append((parsed.path, symbol))
        failure = self.fail.get((parsed.path, symbol))
        if failure is not None:
            raise failure(url)
        start, end = int(query["startTime"]), int(query["endTime"])
        hours = [h for h in range(-24, SLOTS + 24) if start <= START_MS + h * HOUR_MS <= end]
        if parsed.path == "/fapi/v1/klines":
            body = [self._contract(h) for h in hours]
        elif parsed.path == "/fapi/v1/indexPriceKlines":
            body = [self._index(h) for h in hours if h not in self.drop_index]
        else:
            body = [self._official(h, symbol) for h in hours if 0 <= h < SLOTS + 1]
        body = body[: int(query["limit"])]
        return io.BytesIO(json.dumps(body).encode())

    def _contract(self, h):
        open_ms = START_MS + h * HOUR_MS
        open_ = self.contract_open.get(h, "100.1")
        high = max(open_, "100.1", key=Decimal)
        return [
            open_ms,
            open_,
            high,
            "100.1",
            "100.1",
            "1",
            open_ms + HOUR_MS - 1,
            "100",
            1,
            "0.5",
            "50",
            "0",
        ]

    def _index(self, h):
        open_ms = START_MS + h * HOUR_MS
        return [
            open_ms,
            "100",
            "100",
            "100",
            "100",
            "0",
            open_ms + HOUR_MS - 1,
            "0",
            3600,
            "0",
            "0",
            "0",
        ]

    def _official(self, h, pair):
        record = {
            "indexPrice": "100",
            "contractType": "PERPETUAL",
            "basisRate": "0.0010",
            "futuresPrice": "100.1",
            "annualizedBasisRate": "",
            "basis": "0.1",
            "pair": pair,
            "timestamp": START_MS + h * HOUR_MS,
        }
        return {**record, **self.official.get(h, {})}


def _http_error(code, body):
    def make(url):
        return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body.encode()))

    return make


@pytest.fixture
def fake(monkeypatch):
    server = FakeBinance()
    monkeypatch.setattr("urllib.request.urlopen", server.urlopen)
    return server


def _run(tmp_path, symbols=("BTCUSDT",), **kwargs):
    _path, report = cli.run_to_bundle(
        "public-smoke", tmp_path / "out", public_smoke_builder(symbols, lambda: NOW, **kwargs)
    )
    return report


def _checks(report):
    return {c["name"]: (c["status"], c["category"]) for c in report["deterministic"]["checks"]}


def test_clean_run_succeeds_with_hand_derived_numbers(tmp_path, fake):
    report = _run(tmp_path)
    assert (report["status"], report["warning_count"]) == ("succeeded", 0)
    btc = report["deterministic"]["results"]["BTCUSDT"]
    official = btc["official"]
    assert btc["execution"] == "succeeded"
    assert btc["paired_observation_count"] == SLOTS
    assert btc["close_basis"]["min"] == btc["close_basis"]["max"] == "0.1"
    assert btc["close_basis_rate"]["mean"] == "0.001"
    assert btc["request_count"] == 3
    assert (official["record_count"], official["algebraically_consistent"]) == (SLOTS, SLOTS)
    assert official["open_snapshot_matches"] == SLOTS
    assert official["comparable_count"] == SLOTS - 1
    assert official["official_only_timestamps"] == [_ts(0)]
    assert official["observation_only_close_times"] == [_ts(SLOTS)]
    assert Decimal(official["max_abs_rate_difference"]) == 0  # serialized as "0.000"
    assert official["exceeding_timestamps"] == []
    assert set(_checks(report).values()) == {
        ("passed", "execution"),
        ("passed", "data_integrity"),
        ("passed", "formula"),
        ("passed", "anomaly"),
        ("passed", "descriptive"),
    }
    assert report["deterministic"]["config"]["request_budget_per_symbol"] == 6


def test_close_vs_snapshot_exceedance_is_a_visible_warning_not_a_failure(tmp_path, fake):
    # T10 snapshot: futures 100.2 = contract open(T10); our close basis for [T9, T10) stays 0.1
    fake.contract_open[10] = "100.2"
    fake.official[10] = {"futuresPrice": "100.2", "basis": "0.2", "basisRate": "0.0020"}
    report = _run(tmp_path)
    assert (report["status"], report["warning_count"]) == ("succeeded", 1)
    official = report["deterministic"]["results"]["BTCUSDT"]["official"]
    assert official["exceeding_timestamps"] == [_ts(10)]
    assert official["max_abs_rate_difference"] == "0.001"  # 0.002 - 0.001
    assert official["rate_tolerance"] == "0.0001"
    assert _checks(report)["BTCUSDT.close_vs_snapshot_tolerance"] == ("warning", "descriptive")
    assert "warning" in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")


def test_unexplained_snapshot_mismatch_is_an_anomaly_warning(tmp_path, fake):
    fake.official[7] = {"futuresPrice": "100.15", "basis": "0.15", "basisRate": "0.0015"}
    report = _run(tmp_path)
    assert (report["status"], report["warning_count"]) == ("succeeded", 2)
    official = report["deterministic"]["results"]["BTCUSDT"]["official"]
    assert official["open_snapshot_mismatch_timestamps"] == [_ts(7)]
    assert official["exceeding_timestamps"] == [_ts(7)]  # |0.0015 - 0.001| = 0.0005
    assert _checks(report)["BTCUSDT.official_open_snapshot"] == ("warning", "anomaly")


def test_algebraic_inconsistency_is_a_real_failure(tmp_path, fake):
    fake.official[5] = {"basis": "0.3"}  # futures - index = 0.1
    report = _run(tmp_path)
    assert report["status"] == "failed"
    assert _checks(report)["BTCUSDT.official_algebra"] == ("failed", "formula")
    assert report["deterministic"]["results"]["BTCUSDT"]["official"][
        "algebraically_inconsistent_timestamps"
    ] == [_ts(5)]


def test_missing_candle_is_a_data_integrity_failure(tmp_path, fake):
    fake.drop_index.add(20)
    report = _run(tmp_path)
    assert report["status"] == "failed"
    assert _checks(report)["BTCUSDT.candles_complete"] == ("failed", "data_integrity")
    assert report["deterministic"]["results"]["BTCUSDT"]["contract_only_open_times"] == [_ts(20)]


def test_http_error_fails_without_retry(tmp_path, fake):
    fake.fail[("/fapi/v1/indexPriceKlines", "BTCUSDT")] = _http_error(500, "boom")
    report = _run(tmp_path)
    assert report["status"] == "failed"
    assert report["deterministic"]["results"]["BTCUSDT"]["execution"] == "failed"
    assert "HTTP 500" in report["deterministic"]["errors"][0]
    assert fake.calls.count(("/fapi/v1/indexPriceKlines", "BTCUSDT")) == 1


def test_rate_limit_answer_fails_the_symbol(tmp_path, fake):
    fake.fail[("/futures/data/basis", "BTCUSDT")] = _http_error(
        429, '{"code":-1003,"msg":"Too many requests"}'
    )
    report = _run(tmp_path)
    assert report["status"] == "failed"
    assert "HTTP 429" in report["deterministic"]["errors"][0]
    assert _checks(report)["BTCUSDT.execution"] == ("failed", "execution")


def test_timeouts_are_retried_a_bounded_number_of_times(tmp_path, fake):
    def timeout(url):
        return TimeoutError("timed out")

    fake.fail[("/fapi/v1/klines", "BTCUSDT")] = timeout
    report = _run(tmp_path)
    assert report["status"] == "failed"
    assert "ConnectionError" in report["deterministic"]["errors"][0]
    assert fake.calls.count(("/fapi/v1/klines", "BTCUSDT")) == 2  # max_attempts


def test_request_budget_is_enforced_before_any_extra_request(tmp_path, fake):
    report = _run(tmp_path, request_budget_per_symbol=2)
    assert report["status"] == "failed"
    assert "RequestBudgetExceeded" in report["deterministic"]["errors"][0]
    assert len(fake.calls) == 2  # contract + index; the official request was never sent


def test_one_failing_symbol_keeps_the_other_but_fails_the_run(tmp_path, fake):
    fake.fail[("/fapi/v1/indexPriceKlines", "ETHUSDT")] = _http_error(400, "bad")
    report = _run(tmp_path, symbols=("BTCUSDT", "ETHUSDT"))
    results = report["deterministic"]["results"]
    assert report["status"] == "failed"
    assert results["BTCUSDT"]["execution"] == "succeeded"
    assert results["BTCUSDT"]["paired_observation_count"] == SLOTS
    assert results["ETHUSDT"]["execution"] == "failed"
    assert report["deterministic"]["errors"][0].startswith("ETHUSDT: BinanceApiError")


def test_cli_exit_codes_and_no_overwrite(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    fake.contract_open[10] = "100.2"
    fake.official[10] = {"futuresPrice": "100.2", "basis": "0.2", "basisRate": "0.0020"}
    out = tmp_path / "p"
    assert cli.main(["public-smoke", "--allow-network", "--output", str(out)]) == 0
    assert "succeeded with 1 warning(s)" in capsys.readouterr().out
    before = (out / "report.json").read_bytes()
    assert cli.main(["public-smoke", "--allow-network", "--output", str(out)]) == 2
    assert (out / "report.json").read_bytes() == before
    fake.official[5] = {"basis": "0.3"}
    assert cli.main(["public-smoke", "--allow-network", "--output", str(tmp_path / "q")]) == 1
