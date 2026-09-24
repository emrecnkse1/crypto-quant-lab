"""Research run reliability: explicit Decimal context and genuinely read-only store access.

No network. Economic expectations come from research/offline_fixture.py's
hand derivation (999.805 etc.), repeated as literals.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, getcontext, localcontext

import pytest

from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import cli
from crypto_quant_lab.research.decimal_policy import (
    DEFAULT_DECIMAL_CONTEXT,
    build_context,
    normalize_decimal_context,
)
from crypto_quant_lab.research.offline_fixture import build_offline_fixture, fixture_config
from crypto_quant_lab.storage.base import DataCorruptionError, HistoricalCandle, StorageError
from crypto_quant_lab.storage.datasets import binance_usdm_perpetual_contract_trade_dataset
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.storage.sqlite_readonly import connect_read_only, read_only_uri

T0 = datetime(2026, 1, 5, tzinfo=UTC)
NS = ("binance", "usdm_perpetual", "FIXTUREUSDT", "1h")


@pytest.fixture
def fixture_dir(tmp_path):
    return build_offline_fixture(tmp_path / "fixture").directory


def _config_file(directory, **changes):
    config = {**fixture_config(), **changes}
    path = directory / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _report(path):
    return json.loads((path / "report.json").read_text(encoding="utf-8"))


def _snapshot_files(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(directory.iterdir())}


# ================================================================ Decimal contract


def test_default_decimal_context_is_the_documented_one_not_the_ambient_one():
    assert DEFAULT_DECIMAL_CONTEXT == {
        "prec": 28,
        "rounding": "ROUND_HALF_EVEN",
        "Emin": -999999,
        "Emax": 999999,
        "capitals": 1,
        "clamp": 0,
        "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
    }
    with localcontext() as ambient:
        ambient.prec = 3
        assert normalize_decimal_context(None) == DEFAULT_DECIMAL_CONTEXT
    context = build_context(DEFAULT_DECIMAL_CONTEXT)
    assert (context.prec, context.Emin, context.Emax, context.clamp) == (28, -999999, 999999, 0)
    assert not any(context.flags.values())


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({**DEFAULT_DECIMAL_CONTEXT, "rounding": "HALF_UP"}, "rounding"),
        ({**DEFAULT_DECIMAL_CONTEXT, "traps": ["Nope"]}, "traps"),
        ({**DEFAULT_DECIMAL_CONTEXT, "traps": ["Inexact", "Inexact"]}, "repeat"),
        ({**DEFAULT_DECIMAL_CONTEXT, "prec": 0}, "prec"),
        ({**DEFAULT_DECIMAL_CONTEXT, "clamp": 2}, "clamp"),
        ({k: v for k, v in DEFAULT_DECIMAL_CONTEXT.items() if k != "Emax"}, "exactly the keys"),
        ({**DEFAULT_DECIMAL_CONTEXT, "flags": []}, "exactly the keys"),
    ],
)
def test_decimal_context_config_is_validated(tmp_path, value, message):
    with pytest.raises(cli.ConfigError, match=message):
        cli.load_config(_config_file(tmp_path, decimal_context=value))


def test_ambient_context_does_not_change_results_or_hashes(tmp_path):
    assert cli.main(["offline-smoke", "--output", str(tmp_path / "default")]) == 0
    with localcontext() as ambient:
        ambient.prec = 5
        ambient.rounding = ROUND_DOWN
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        assert cli.main(["offline-smoke", "--output", str(tmp_path / "hostile")]) == 0
        assert (getcontext().prec, getcontext().rounding) == (5, ROUND_DOWN)
        assert getcontext().traps[Inexact] and getcontext().traps[Rounded]
    default, hostile = _report(tmp_path / "default"), _report(tmp_path / "hostile")
    assert default["deterministic_sha256"] == hostile["deterministic_sha256"]
    assert (
        default["deterministic"]["run_input_sha256"] == hostile["deterministic"]["run_input_sha256"]
    )
    carry = hostile["deterministic"]["results"]["funding_research"]["runs"]["carry_s2bp_l-1bp"]
    assert Decimal(carry["windows"][0]["final_equity"]) == Decimal("999.805")
    assert hostile["run_metadata"]["ambient_decimal_context"]["prec"] == 5
    assert default["deterministic"]["config"]["decimal_context"] == DEFAULT_DECIMAL_CONTEXT


def test_ambient_precision_really_breaks_the_unguarded_engine_path(fixture_dir):
    """The experiment behind the fix: the same section outside the policy context."""
    config = cli.load_config(
        _config_file(
            fixture_dir,
            stores={"contract": "contract.db", "index": "index.db", "funding": "funding.db"},
        )
    )
    with localcontext() as ambient:
        ambient.prec = 5
        with pytest.raises(ValueError, match="total_pnl consistency invariant"):
            cli.funding_section(config)  # funding cash 999.805 needs 6 digits
    assert cli._in_config_context(config, cli.funding_section).results["runs"]


def test_changing_the_computation_context_changes_the_fingerprints(fixture_dir, tmp_path):
    stores = {"contract": "contract.db", "index": "index.db", "funding": "funding.db"}
    wide = {**DEFAULT_DECIMAL_CONTEXT, "prec": 50}
    narrow = {**DEFAULT_DECIMAL_CONTEXT, "prec": 5}
    runs = {}
    for name, context in (("default", None), ("wide", wide), ("narrow", narrow)):
        changes = {"stores": stores} | ({} if context is None else {"decimal_context": context})
        path = _config_file(fixture_dir, **changes)
        out = tmp_path / name
        runs[name] = (
            cli.main(["funding-research", "--config", str(path), "--output", str(out)]),
            _report(out),
        )
    (code_d, default), (code_w, wide_r), (code_n, narrow_r) = runs.values()
    assert (code_d, code_w, code_n) == (0, 0, 1)
    hashes = {r["deterministic"]["run_input_sha256"] for r in (default, wide_r, narrow_r)}
    assert len(hashes) == 3
    first = default["deterministic"]["results"]["runs"]["carry_s2bp_l-1bp"]["windows"][0]
    first_w = wide_r["deterministic"]["results"]["runs"]["carry_s2bp_l-1bp"]["windows"][0]
    assert Decimal(first["final_equity"]) == Decimal(first_w["final_equity"]) == Decimal("999.805")
    assert narrow_r["status"] == "failed"
    assert "total_pnl consistency invariant" in narrow_r["deterministic"]["errors"][0]


def test_input_and_output_fingerprints_have_different_roles(fixture_dir, tmp_path):
    stores = {"contract": "contract.db", "index": "index.db", "funding": "funding.db"}
    path = _config_file(fixture_dir, stores=stores)
    assert cli.main(["basis-report", "--config", str(path), "--output", str(tmp_path / "a")]) == 0
    assert cli.main(["inspect", "--config", str(path), "--output", str(tmp_path / "b")]) == 0
    basis, inspect = _report(tmp_path / "a"), _report(tmp_path / "b")
    # same config, different commands: same config hash, different outputs
    assert basis["deterministic"]["config_sha256"] == inspect["deterministic"]["config_sha256"]
    assert basis["deterministic_sha256"] != inspect["deterministic_sha256"]
    contract_fp = {
        i["role"]: i["logical_fingerprint_sha256"] for i in basis["deterministic"]["inputs"]
    }
    inspect_fp = {
        i["role"]: i["logical_fingerprint_sha256"] for i in inspect["deterministic"]["inputs"]
    }
    assert contract_fp["contract"] == inspect_fp["contract"]


# ================================================================ read-only store access


def test_read_only_store_rejects_every_write(fixture_dir):
    store = SQLiteHistoricalCandleStore.open_read_only(fixture_dir / "contract.db")
    other = HistoricalCandle(
        exchange="binance",
        market_type="spot",
        candle=Candle(
            "FIXTUREUSDT", "1h", T0, Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(0)
        ),
    )
    with pytest.raises(StorageError, match="readonly"):
        store.write_batch([other])
    with pytest.raises(StorageError, match="readonly"):
        store.write_ingestion_batch(
            [],
            dataset=binance_usdm_perpetual_contract_trade_dataset("OTHERUSDT", "1h"),
            covered_start=T0,
            covered_end=T0.replace(hour=1),
        )
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store._connection.execute("CREATE TABLE sneaky (x)")
    assert store.query_dataset(*NS).price_kind == "contract_trade"
    store.close()
    funding = SQLiteHistoricalFundingStore.open_read_only(fixture_dir / "funding.db")
    with pytest.raises(StorageError, match="readonly"):
        funding.write_ingestion_batch(
            [],
            exchange="binance",
            market_type="x",
            symbol="Y",
            covered_start=T0,
            covered_end=T0.replace(hour=1),
        )
    funding.close()


def test_read_only_open_never_creates_files_or_schema(tmp_path):
    for cls in (SQLiteHistoricalCandleStore, SQLiteHistoricalFundingStore):
        with pytest.raises(FileNotFoundError):
            cls.open_read_only(tmp_path / "missing.db")
    assert list(tmp_path.iterdir()) == []
    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy)
    connection.execute("CREATE TABLE historical_candles (x TEXT)")
    connection.commit()
    connection.close()
    before = _snapshot_files(tmp_path)
    with pytest.raises(StorageError, match="lacks tables"):
        SQLiteHistoricalCandleStore.open_read_only(legacy)
    wrong = tmp_path / "wrong.db"
    connection = sqlite3.connect(wrong)
    for table in cli.CANDLE_TABLES:
        connection.execute(f"CREATE TABLE {table} (x TEXT)")
    connection.commit()
    connection.close()
    wrong_before = wrong.read_bytes()
    with pytest.raises(DataCorruptionError, match="schema mismatch"):
        SQLiteHistoricalCandleStore.open_read_only(wrong)
    assert wrong.read_bytes() == wrong_before
    assert {k: v for k, v in _snapshot_files(tmp_path).items() if k != "wrong.db"} == before


def test_normal_ingestion_still_writes_while_a_reader_is_open(fixture_dir):
    reader = SQLiteHistoricalCandleStore.open_read_only(fixture_dir / "contract.db")
    writer = SQLiteHistoricalCandleStore(fixture_dir / "contract.db")
    spot = HistoricalCandle(
        exchange="binance",
        market_type="spot",
        candle=Candle(
            "FIXTUREUSDT", "1h", T0, Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(0)
        ),
    )
    writer.write_batch([spot])
    assert reader.query("binance", "spot", "FIXTUREUSDT", "1h", T0, T0.replace(hour=1)) == [spot]
    writer.close()
    reader.close()


def test_snapshot_is_consistent_in_wal_mode_and_blocks_writers_in_rollback_mode(fixture_dir):
    path = fixture_dir / "contract.db"
    spot = HistoricalCandle(
        exchange="binance",
        market_type="spot",
        candle=Candle(
            "FIXTUREUSDT", "1h", T0, Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(0)
        ),
    )
    rollback_reader = SQLiteHistoricalCandleStore.open_read_only(path)
    blocked = sqlite3.connect(path, timeout=0.05)
    with (
        rollback_reader.read_snapshot(),
        pytest.raises(sqlite3.OperationalError, match="locked"),
    ):
        blocked.execute("INSERT INTO candle_datasets VALUES ('a','b','c','d','e','f')")
        blocked.commit()
    blocked.rollback()
    blocked.close()
    rollback_reader.close()

    switch = sqlite3.connect(path)
    assert switch.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    switch.close()
    writer = SQLiteHistoricalCandleStore(path)
    reader = SQLiteHistoricalCandleStore.open_read_only(path)
    args = ("binance", "spot", "FIXTUREUSDT", "1h", T0, T0.replace(hour=1))
    with reader.read_snapshot():
        assert reader.query(*args) == []
        writer.write_batch([spot])  # a WAL writer is not blocked by the reader
        assert reader.query(*args) == []  # still the snapshot taken at BEGIN
    assert reader.query(*args) == [spot]
    writer.close()
    reader.close()


def test_uri_handles_spaces_unicode_and_uri_special_characters(tmp_path):
    directory = tmp_path / "a b #%&=üş"
    directory.mkdir()
    fixture = build_offline_fixture(directory / "fx ;#%")
    uri = read_only_uri(fixture.directory / "contract.db")
    assert "%20" in uri and "%23" in uri and "%25" in uri and "%C3%BC" in uri
    assert uri.endswith("contract.db?mode=ro")
    store = SQLiteHistoricalCandleStore.open_read_only(fixture.directory / "contract.db")
    assert store.query_dataset(*NS).price_kind == "contract_trade"
    store.close()
    connection = connect_read_only(fixture.directory / "index.db")
    connection.close()


def test_hard_link_alias_of_one_store_is_rejected(fixture_dir):
    os.link(fixture_dir / "contract.db", fixture_dir / "alias.db")
    stores = {"contract": "contract.db", "index": "alias.db"}
    with pytest.raises(cli.ConfigError, match="same physical file"):
        cli.load_config(_config_file(fixture_dir, stores=stores))


def test_commands_leave_input_databases_untouched(fixture_dir, tmp_path):
    stores = {"contract": "contract.db", "index": "index.db", "funding": "funding.db"}
    path = _config_file(fixture_dir, stores=stores)
    before = _snapshot_files(fixture_dir)
    for command in ("inspect", "basis-report", "funding-research"):
        out = tmp_path / command
        assert cli.main([command, "--config", str(path), "--output", str(out)]) == 0
    assert cli.main(["doctor", "--config", str(path)]) == 0
    assert _snapshot_files(fixture_dir) == before  # bytes, mtimes, and no -wal/-shm/-journal
