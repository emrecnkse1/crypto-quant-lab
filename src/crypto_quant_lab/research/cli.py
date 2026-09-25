"""Reproducible research commands (FUNDING_RESEARCH_SPEC.md Bölüm 17).

    python -m crypto_quant_lab.research <command> ...

Commands (offline unless stated): `doctor`, `inspect`, `basis-report`,
`funding-research`, `offline-smoke`, `multileg-replay` / `multileg-example`
(research/multileg_config.py), and the explicitly opt-in network command
`public-smoke --allow-network`. Every command only orchestrates
production APIs — no formula, accounting or strategy logic lives here.

Input databases are opened through `open_read_only` (a `mode=ro` SQLite
connection with `query_only`; no file, table or migration is ever created)
and every store's queries and fingerprints run inside one read snapshot.
Each computation runs inside a FRESH Decimal context built from the config's
`decimal_context` (research/decimal_policy.py). Result bundles go to a NEW
output directory (see research/report.py); nothing is overwritten.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

from crypto_quant_lab.backtest.costs import (
    CompositeCostModel,
    CostModel,
    ProportionalCommissionModel,
    ProportionalSlippageCostModel,
    ProportionalSpreadCostModel,
    ZeroCostModel,
)
from crypto_quant_lab.backtest.models import BacktestConfig
from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.quality import build_funding_data_quality_report_from_store
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.research.basis import CloseBasisHistory, load_close_basis_history
from crypto_quant_lab.research.decimal_policy import build_context, normalize_decimal_context
from crypto_quant_lab.research.diagnostics import diagnose_funding_research_trial
from crypto_quant_lab.research.funding_carry import (
    funding_carry_candidate,
    load_funding_signal_history,
    no_trade_control_candidate,
)
from crypto_quant_lab.research.report import (
    OutputBundle,
    build_report,
    canonical_json,
    check,
    fingerprint,
    sha256_hex,
)
from crypto_quant_lab.research.usdm_perpetual import evaluate_usdm_perpetual_funding_research
from crypto_quant_lab.storage.base import DataCorruptionError, StorageError
from crypto_quant_lab.storage.datasets import (
    BINANCE,
    BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
    BINANCE_USDM_KLINES_SOURCE,
    USDM_PERPETUAL,
    CandleDataset,
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
    coverage_contains,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.storage.sqlite_readonly import connect_read_only, existing_tables
from crypto_quant_lab.validation.metrics import compute_stage1_metrics, compute_stage2_metrics
from crypto_quant_lab.validation.windows import TemporalWindow

CONFIG_VERSION = 1
CANDLE_TABLES = ("historical_candles", "candle_datasets", "candle_coverage")
FUNDING_TABLES = ("historical_funding_events", "historical_funding_coverage")
PUBLIC_SMOKE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
CANONICAL_SOURCES = {
    "contract": BINANCE_USDM_KLINES_SOURCE,
    "index": BINANCE_USDM_INDEX_PRICE_KLINES_SOURCE,
}
_STATS = Context(prec=34, rounding=ROUND_HALF_EVEN)

BASIS_DOES_NOT_PROVE = [
    (
        "close basis is a descriptive research feature, not a tradable spread: the index is not "
        "a tradable leg and no hedge, basis trade or PnL is modelled"
    ),
    "no profitability, arbitrage opportunity or trading signal is implied",
]
FUNDING_DOES_NOT_PROVE = [
    (
        "a single pre-fixed candidate on the given windows; no out-of-sample, multiple-testing or "
        "overfitting control is applied here (FAZ6C open items remain open)"
    ),
    (
        "the engine is single-leg: this is a directional perpetual hypothesis, not a hedged "
        "funding/basis carry"
    ),
    "test counts and zero-error runs are not evidence of profitability",
]


class ConfigError(ValueError):
    """A config field is missing or invalid; the message names the field."""


# ---------------------------------------------------------------- config


def _reject_float(text: str) -> object:
    raise ConfigError(
        f"JSON numbers with a fraction/exponent are not allowed ({text}); "
        'write decimals as strings, e.g. "0.0002"'
    )


def _reject_constant(text: str) -> object:
    raise ConfigError(f"JSON constant {text} is not allowed")


def _field(mapping: dict, key: str, where: str) -> object:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"missing config field {where}{key}")
    return mapping[key]


def _time(mapping: dict, key: str, where: str = "") -> datetime:
    raw = _field(mapping, key, where)
    if not isinstance(raw, str):
        raise ConfigError(f"{where}{key} must be an ISO-8601 string")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigError(f"{where}{key} is not ISO-8601: {raw!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfigError(f"{where}{key} must carry an explicit offset (e.g. 'Z'): {raw!r}")
    return value.astimezone(UTC)


def _decimal(mapping: dict, key: str, where: str) -> Decimal:
    raw = _field(mapping, key, where)
    if not isinstance(raw, str):
        raise ConfigError(f'{where}{key} must be a decimal string, e.g. "0.0002"')
    try:
        value = Decimal(raw)
    except ArithmeticError as exc:
        raise ConfigError(f"{where}{key} is not a decimal: {raw!r}") from exc
    if not value.is_finite():
        raise ConfigError(f"{where}{key} must be finite")
    return value


def _int(mapping: dict, key: str, where: str, *, minimum: int) -> int:
    raw = _field(mapping, key, where)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ConfigError(f"{where}{key} must be an integer >= {minimum}")
    return raw


@dataclass(frozen=True, slots=True)
class FundingResearchConfig:
    coverage_start: datetime
    coverage_end: datetime
    publication_lag: timedelta
    windows: tuple[TemporalWindow, ...]
    initial_cash: Decimal
    position_quantity: Decimal
    candidate_id: str
    short_entry_rate: Decimal
    long_entry_rate: Decimal
    max_funding_age: timedelta
    include_no_trade_control: bool
    commission_rate: Decimal
    half_spread_rate: Decimal
    slippage_rate: Decimal


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    raw: dict
    sha256: str
    base_dir: Path
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    as_of: datetime
    stores: dict[str, Path] = field(default_factory=dict)
    funding: FundingResearchConfig | None = None
    decimal_context: dict = field(default_factory=lambda: normalize_decimal_context(None))
    sources: dict = field(default_factory=lambda: dict(CANONICAL_SOURCES))

    def expected_dataset(self, role: str) -> CandleDataset:
        """The exact dataset identity a store role must have registered (source included)."""
        if role == "contract":
            return binance_usdm_perpetual_contract_trade_dataset(
                self.symbol, self.timeframe, source=self.sources["contract"]
            )
        if role == "index":
            return binance_usdm_index_price_dataset(
                self.symbol, self.timeframe, source=self.sources["index"]
            )
        raise ValueError(f"unknown candle role {role!r}")

    def effective(self) -> dict:
        """The config as recorded in reports.

        Store paths are reduced to file names; `decimal_context` is always the
        RESOLVED context (the documented default when the config omits it), so
        the report fingerprint changes whenever the arithmetic would.
        """
        recorded = json.loads(json.dumps(self.raw))
        recorded["stores"] = {role: Path(path).name for role, path in self.raw["stores"].items()}
        recorded["decimal_context"] = self.decimal_context
        return recorded


def parse_config(raw: dict, *, base_dir: Path) -> ResearchConfig:
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    if raw.get("config_version") != CONFIG_VERSION:
        raise ConfigError(f"config_version must be {CONFIG_VERSION}")
    symbol = _field(raw, "symbol", "")
    if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isupper():
        raise ConfigError('symbol must be an upper-case alphanumeric pair, e.g. "BTCUSDT"')
    timeframe = _field(raw, "timeframe", "")
    try:
        candle_duration(timeframe)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"timeframe: {exc}") from exc
    start, end, as_of = _time(raw, "start"), _time(raw, "end"), _time(raw, "as_of")
    if not start < end:
        raise ConfigError("start must be before end")
    if not (is_grid_aligned(start, timeframe) and is_grid_aligned(end, timeframe)):
        raise ConfigError(f"start/end must be aligned to the {timeframe!r} UTC grid")
    if as_of < end:
        raise ConfigError("as_of must be >= end (the requested range must be closed as of as_of)")
    stores_raw = _field(raw, "stores", "")
    if not isinstance(stores_raw, dict) or not stores_raw:
        raise ConfigError('stores must be an object like {"contract": "contract.db"}')
    stores = {}
    for role, value in stores_raw.items():
        if role not in ("contract", "index", "funding"):
            raise ConfigError(f"stores.{role}: unknown role (use contract, index, funding)")
        if not isinstance(value, str) or not value:
            raise ConfigError(f"stores.{role} must be a non-empty path string")
        stores[role] = (base_dir / value).resolve()
    resolved = list(stores.values())
    if len(set(resolved)) != len(resolved):
        raise ConfigError("stores: two roles point to the same file")
    existing = [path for path in resolved if path.is_file()]
    for i, first in enumerate(existing):
        for second in existing[i + 1 :]:
            if os.path.samefile(first, second):
                raise ConfigError("stores: two roles point to the same physical file (alias/link)")
    try:
        decimal_context = normalize_decimal_context(raw.get("decimal_context"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    funding = None
    if "funding_research" in raw:
        fr = raw["funding_research"]
        w = "funding_research."
        windows_raw = _field(fr, "windows", w)
        if not isinstance(windows_raw, list) or not windows_raw:
            raise ConfigError(f"{w}windows must be a non-empty list of [start, end] pairs")
        windows = []
        for index, pair in enumerate(windows_raw):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ConfigError(f"{w}windows[{index}] must be [start, end]")
            window_start = _time({"start": pair[0]}, "start", f"{w}windows[{index}].")
            window_end = _time({"end": pair[1]}, "end", f"{w}windows[{index}].")
            if not start <= window_start < window_end <= end:
                raise ConfigError(f"{w}windows[{index}] must lie inside [start, end)")
            windows.append(TemporalWindow(start=window_start, end=window_end))
        candidate = _field(fr, "candidate", w)
        cw = f"{w}candidate."
        candidate_id = _field(candidate, "candidate_id", cw)
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ConfigError(f"{cw}candidate_id must be a non-empty string")
        cost = _field(fr, "cost", w)
        include_control = _field(fr, "include_no_trade_control", w)
        if not isinstance(include_control, bool):
            raise ConfigError(f"{w}include_no_trade_control must be true or false")
        funding = FundingResearchConfig(
            coverage_start=_time(fr, "funding_coverage_start", w),
            coverage_end=_time(fr, "funding_coverage_end", w),
            publication_lag=timedelta(seconds=_int(fr, "publication_lag_seconds", w, minimum=0)),
            windows=tuple(windows),
            initial_cash=_decimal(fr, "initial_cash", w),
            position_quantity=_decimal(fr, "position_quantity", w),
            candidate_id=candidate_id,
            short_entry_rate=_decimal(candidate, "short_entry_rate", cw),
            long_entry_rate=_decimal(candidate, "long_entry_rate", cw),
            max_funding_age=timedelta(
                hours=_int(candidate, "max_funding_age_hours", cw, minimum=1)
            ),
            include_no_trade_control=include_control,
            commission_rate=_decimal(cost, "commission_rate", f"{w}cost."),
            half_spread_rate=_decimal(cost, "half_spread_rate", f"{w}cost."),
            slippage_rate=_decimal(cost, "slippage_rate", f"{w}cost."),
        )
        if funding.position_quantity <= 0 or funding.initial_cash <= 0:
            raise ConfigError(f"{w}initial_cash and position_quantity must be > 0")
        if not funding.long_entry_rate < funding.short_entry_rate:
            raise ConfigError(f"{cw}long_entry_rate must be below short_entry_rate")
        if not funding.coverage_start < funding.coverage_end:
            raise ConfigError(f"{w}funding_coverage_start must be before funding_coverage_end")
        if min(funding.commission_rate, funding.half_spread_rate, funding.slippage_rate) < 0:
            raise ConfigError(f"{w}cost rates must be >= 0")
    return ResearchConfig(
        raw=raw,
        sha256=sha256_hex(canonical_json(raw)),
        base_dir=base_dir,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        as_of=as_of,
        stores=stores,
        funding=funding,
        decimal_context=decimal_context,
        sources=_parse_sources(raw.get("sources")),
    )


def _parse_sources(raw: object) -> dict:
    """Optional `sources` {contract?, index?}: expected dataset source labels.

    Omitted roles default to the canonical Binance endpoints (the behavior of
    configs written before this field existed). Labels are compared exactly by
    the readers; nothing is normalized or guessed.
    """
    sources = dict(CANONICAL_SOURCES)
    if raw is None:
        return sources
    if not isinstance(raw, dict):
        raise ConfigError('sources must be an object like {"contract": "synthetic:..."}')
    for role, value in raw.items():
        if role not in CANONICAL_SOURCES:
            raise ConfigError(f"sources.{role}: unknown role (use contract, index)")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"sources.{role} must be a non-empty string")
        sources[role] = value
    return sources


def load_config(path: Path) -> ResearchConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}") from exc
    return parse_config(raw, base_dir=path.resolve().parent)


# ---------------------------------------------------------------- read-only store access


def sqlite_tables(path: Path) -> set[str]:
    """Table names of an EXISTING SQLite file via a genuinely read-only connection."""
    connection = connect_read_only(path)
    try:
        return existing_tables(connection)
    finally:
        connection.close()


def open_candle_store(path: Path) -> SQLiteHistoricalCandleStore:
    """Read-only candle store: never creates the file, a table or a migration."""
    return SQLiteHistoricalCandleStore.open_read_only(path)


def open_funding_store(path: Path) -> SQLiteHistoricalFundingStore:
    """Read-only funding store: never creates the file, a table or a migration."""
    return SQLiteHistoricalFundingStore.open_read_only(path)


@contextmanager
def read_stores(*opened) -> Iterator[None]:
    """Hold one read snapshot per store for the whole block, then close them all.

    Each store is its own SQLite file, so each has its own snapshot; there is
    no atomic snapshot ACROSS files. Every query and fingerprint of one store
    inside the block comes from the same committed state of that store.
    """
    with ExitStack() as stack:
        for store in opened:
            stack.callback(store.close)
        for store in opened:
            stack.enter_context(store.read_snapshot())
        yield


def _require_store(config: ResearchConfig, role: str) -> Path:
    if role not in config.stores:
        raise ConfigError(f"stores.{role} is required for this command")
    return config.stores[role]


# ---------------------------------------------------------------- payload sections


@dataclass
class Section:
    results: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    does_not_prove: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _missing_open_times(store, dataset: CandleDataset, start: datetime, end: datetime) -> list:
    present = {r.candle.open_time for r in store.query(*dataset.namespace, start, end)}
    step = candle_duration(dataset.timeframe)
    missing, cursor = [], start
    while cursor < end:
        if cursor not in present:
            missing.append(cursor)
        cursor += step
    return missing


def candle_input(role: str, path: Path, store, dataset: CandleDataset, start, end) -> dict:
    registered = store.query_dataset(*dataset.namespace)
    coverage = store.query_coverage(*dataset.namespace, start, end)
    rows = store.query(*dataset.namespace, start, end)
    candles = [
        [
            r.candle.open_time,
            r.candle.open,
            r.candle.high,
            r.candle.low,
            r.candle.close,
            r.candle.volume,
        ]
        for r in rows
    ]
    registered_view = (
        None
        if registered is None
        else {
            "exchange": registered.exchange,
            "market_type": registered.market_type,
            "symbol": registered.symbol,
            "timeframe": registered.timeframe,
            "price_kind": registered.price_kind,
            "source": registered.source,
        }
    )
    coverage_view = [[c.start_time, c.end_time] for c in coverage]
    return {
        "role": role,
        "file_name": path.name,
        "registered_dataset": registered_view,
        "coverage_intervals": coverage_view,
        "candle_count": len(candles),
        "logical_fingerprint_sha256": fingerprint(
            {"dataset": registered_view, "coverage": coverage_view, "candles": candles}
        ),
    }


def _candle_checks(role: str, entry: dict, expected: CandleDataset, store, start, end) -> list:
    registered = entry["registered_dataset"]
    checks = []
    if registered is None:
        checks.append(
            check(
                f"{role}.provenance",
                "failed",
                f"namespace {list(expected.namespace)} has no registered provenance",
                category="data_integrity",
            )
        )
    elif (registered["price_kind"], registered["source"]) != (expected.price_kind, expected.source):
        checks.append(
            check(
                f"{role}.provenance",
                "failed",
                f"registered as {registered['price_kind']} from {registered['source']};"
                f" expected {expected.price_kind}",
                category="data_integrity",
            )
        )
    else:
        checks.append(
            check(
                f"{role}.provenance",
                "passed",
                f"{expected.price_kind} from {expected.source}",
                category="data_integrity",
            )
        )
    covered = coverage_contains(store.query_coverage(*expected.namespace, start, end), start, end)
    checks.append(
        check(
            f"{role}.coverage",
            "passed" if covered else "failed",
            "authoritative coverage contains [start, end)"
            if covered
            else "authoritative coverage does NOT contain [start, end)",
            category="data_integrity",
        )
    )
    return checks


def _funding_input(store, path: Path, symbol: str, start: datetime, end: datetime):
    report = build_funding_data_quality_report_from_store(
        store,
        exchange=BINANCE,
        market_type=USDM_PERPETUAL,
        symbol=symbol,
        requested_start=start,
        requested_end=end,
    )
    events = store.query_events(
        exchange=BINANCE,
        market_type=USDM_PERPETUAL,
        symbol=symbol,
        start_time=start,
        end_time=end,
    )
    rows = [
        [
            e.funding.event_time,
            e.funding.funding_rate,
            e.funding.reference_price,
            e.funding.rate_type,
        ]
        for e in events
    ]
    entry = {
        "role": "funding",
        "file_name": path.name,
        "requested_range": [start, end],
        "event_count": len(rows),
        "coverage_gaps": [list(gap) for gap in report.coverage_gaps],
        "quality_status": report.overall_status,
        "logical_fingerprint_sha256": fingerprint({"events": rows, "gaps": report.coverage_gaps}),
    }
    return entry, report, events


def inspect_section(config: ResearchConfig) -> Section:
    section = Section(
        limitations=[
            (
                "duplicates cannot exist inside one store: the candle primary "
                "key is (exchange, market_type, symbol, timeframe, open_time)"
            )
        ]
    )
    expected = {role: config.expected_dataset(role) for role in ("contract", "index")}
    for role in ("contract", "index"):
        if role not in config.stores:
            section.checks.append(check(f"{role}.store", "skipped", f"stores.{role} not set"))
            continue
        path = config.stores[role]
        store = open_candle_store(path)
        with read_stores(store):
            entry = candle_input(role, path, store, expected[role], config.start, config.end)
            entry["missing_open_times"] = _missing_open_times(
                store, expected[role], config.start, config.end
            )
            section.inputs.append(entry)
            section.checks += _candle_checks(
                role, entry, expected[role], store, config.start, config.end
            )
    if "funding" in config.stores:
        start, end = config.start, config.end
        if config.funding is not None:
            start, end = config.funding.coverage_start, config.funding.coverage_end
        store = open_funding_store(config.stores["funding"])
        with read_stores(store):
            entry, report, _ = _funding_input(
                store, config.stores["funding"], config.symbol, start, end
            )
        section.inputs.append(entry)
        section.checks.append(
            check(
                "funding.quality",
                "passed" if report.overall_status == "PASS" else "failed",
                f"quality {report.overall_status}, {report.coverage_gap_count} coverage gap(s)",
                category="data_integrity",
            )
        )
    return section


def _stats(values: list[Decimal]) -> dict | None:
    if not values:
        return None
    total = Decimal(0)
    for value in values:
        total = _STATS.add(total, value)
    return {
        "min": min(values),
        "max": max(values),
        "mean": _STATS.divide(total, Decimal(len(values))),
    }


def basis_results(history: CloseBasisHistory, end: datetime) -> dict:
    observations = history.visible_at(end)
    basis = [o.close_basis for o in observations]
    return {
        "paired_observation_count": len(observations),
        "contract_only_open_times": list(history.contract_only_open_times),
        "index_only_open_times": list(history.index_only_open_times),
        "both_missing_open_times": list(history.both_missing_open_times),
        "close_basis": _stats(basis),
        "close_basis_rate": _stats([o.close_basis_rate for o in observations]),
        "premium_count": sum(1 for v in basis if v > 0),
        "discount_count": sum(1 for v in basis if v < 0),
        "zero_count": sum(1 for v in basis if v == 0),
        "observations": [
            {
                "open_time": o.open_time,
                "close_time": o.close_time,
                "available_at": o.available_at,
                "contract_close": o.contract_close,
                "index_close": o.index_close,
                "close_basis": o.close_basis,
                "close_basis_rate": o.close_basis_rate,
            }
            for o in observations
        ],
    }


BASIS_LIMITATIONS = [
    (
        "close_basis = contract close - index close; close_basis_rate = close_basis / index close "
        "(34 significant digits, ROUND_HALF_EVEN) — a local definition, not Binance's basisRate"
    ),
    "exact open_time pairing only; a slot missing on either side is reported, never filled",
    "an observation is available only at its interval close (open_time + timeframe)",
]


def basis_history_and_section(config: ResearchConfig) -> tuple[CloseBasisHistory, Section]:
    """Open both stores read-only, then query, fingerprint and pair inside their snapshots."""
    contract_path = _require_store(config, "contract")
    index_path = _require_store(config, "index")
    if (
        contract_path.is_file()
        and index_path.is_file()
        and os.path.samefile(contract_path, index_path)
    ):
        raise ConfigError("stores.contract and stores.index are the same physical file")
    contract = open_candle_store(contract_path)
    try:
        index = open_candle_store(index_path)
    except Exception:
        contract.close()
        raise
    section = Section(
        limitations=list(BASIS_LIMITATIONS), does_not_prove=list(BASIS_DOES_NOT_PROVE)
    )
    with read_stores(contract, index):
        for role, path, store, dataset in (
            (
                "contract",
                contract_path,
                contract,
                config.expected_dataset("contract"),
            ),
            (
                "index",
                index_path,
                index,
                config.expected_dataset("index"),
            ),
        ):
            section.inputs.append(
                candle_input(role, path, store, dataset, config.start, config.end)
            )
        history = load_close_basis_history(
            contract,
            index,
            symbol=config.symbol,
            timeframe=config.timeframe,
            start_time=config.start,
            end_time=config.end,
            contract_source=config.sources["contract"],
            index_source=config.sources["index"],
        )
    section.results = basis_results(history, config.end)
    section.checks.append(
        check(
            "basis.provenance_and_coverage",
            "passed",
            "both stores registered and cover [start, end)",
            category="data_integrity",
        )
    )
    gaps = (
        len(history.contract_only_open_times)
        + len(history.index_only_open_times)
        + len(history.both_missing_open_times)
    )
    section.checks.append(
        check(
            "basis.pairing_gaps",
            "passed",
            f"{gaps} unpaired slot(s) reported (not a failure; not filled)",
            category="descriptive",
        )
    )
    return history, section


def basis_section(config: ResearchConfig) -> Section:
    return basis_history_and_section(config)[1]


def _cost_model(fr: FundingResearchConfig) -> CostModel:
    components = []
    if fr.commission_rate:
        components.append(ProportionalCommissionModel(rate=fr.commission_rate))
    if fr.half_spread_rate:
        components.append(ProportionalSpreadCostModel(half_spread_rate=fr.half_spread_rate))
    if fr.slippage_rate:
        components.append(ProportionalSlippageCostModel(rate=fr.slippage_rate))
    return CompositeCostModel(components=tuple(components)) if components else ZeroCostModel()


def _window_row(window_result, diagnostics) -> dict:
    result = window_result.result
    stage1 = compute_stage1_metrics(result)
    row = {
        "window": [window_result.window.start, window_result.window.end],
        "final_equity": result.final_equity,
        "total_pnl": result.total_pnl,
        "total_cost_including_funding": result.total_cost,
        "fill_count": result.fill_count,
        "trade_count": result.trade_count,
        "total_return": stage1.total_return,
        "max_drawdown": stage1.max_drawdown,
        "diagnostics": {
            "decisions_evaluated": diagnostics.decisions_evaluated,
            "signal_visible": diagnostics.signal_visible,
            "fresh_signal": diagnostics.fresh_signal,
            "threshold_met": diagnostics.threshold_met,
            "reason_counts": dict(diagnostics.reason_counts),
            "target_changes": diagnostics.target_changes,
            "executable_target_changes": diagnostics.executable_target_changes,
            "final_decision_unexecuted": diagnostics.final_decision_unexecuted,
            "consistent_with_engine_fill_count": diagnostics.consistent_with_engine,
        },
    }
    try:
        row["stage2_sharpe_per_observation"] = compute_stage2_metrics(result).sharpe_ratio
    except ValueError as exc:
        row["stage2_sharpe_per_observation"] = None
        row["stage2_undefined_reason"] = str(exc)
    return row


def funding_section(config: ResearchConfig) -> Section:
    fr = config.funding
    if fr is None:
        raise ConfigError("funding_research section is required for this command")
    contract_path = _require_store(config, "contract")
    funding_path = _require_store(config, "funding")
    contract = open_candle_store(contract_path)
    try:
        funding = open_funding_store(funding_path)
    except Exception:
        contract.close()
        raise
    section = Section(
        limitations=[
            (
                "engine: single instrument, next-open fills, funding applied once by the "
                "replay engine (funding_required=True); decisions at candle close"
            ),
            (
                f"cost model: commission {fr.commission_rate}, half-spread "
                f"{fr.half_spread_rate}, slippage {fr.slippage_rate} per fill on notional "
                "(config assumptions, not verified exchange fees)"
            ),
            "diagnostic reasons are the policy's own rule branches; no risk filter exists",
        ],
        does_not_prove=list(FUNDING_DOES_NOT_PROVE),
    )
    with read_stores(contract, funding):
        section.inputs.append(
            candle_input(
                "contract",
                contract_path,
                contract,
                config.expected_dataset("contract"),
                config.start,
                config.end,
            )
        )
        history = load_funding_signal_history(
            funding,
            exchange=BINANCE,
            market_type=USDM_PERPETUAL,
            symbol=config.symbol,
            coverage_start=fr.coverage_start,
            coverage_end=fr.coverage_end,
            publication_lag=fr.publication_lag,
        )
        entry, _, events = _funding_input(
            funding, funding_path, config.symbol, fr.coverage_start, fr.coverage_end
        )
        section.inputs.append(entry)
        rates = [e.funding.funding_rate for e in events]
        candidates = [
            funding_carry_candidate(
                fr.candidate_id,
                short_entry_rate=fr.short_entry_rate,
                long_entry_rate=fr.long_entry_rate,
                max_funding_age=fr.max_funding_age,
                publication_lag=fr.publication_lag,
            )
        ]
        if fr.include_no_trade_control:
            candidates.append(no_trade_control_candidate("no_trade_control"))
        runs = {}
        for candidate in candidates:
            trial = evaluate_usdm_perpetual_funding_research(
                contract,
                funding,
                history,
                candidate,
                windows=fr.windows,
                timeframe=config.timeframe,
                as_of_time=config.as_of,
                config=BacktestConfig(
                    initial_cash=fr.initial_cash, position_quantity=fr.position_quantity
                ),
                cost_model=_cost_model(fr),
                funding_model=LinearFundingModel(),
                contract_source=config.sources["contract"],
            )
            diagnostics = diagnose_funding_research_trial(contract, history, trial)
            runs[candidate.candidate_id] = {
                "parameters": [[k, v] for k, v in candidate.parameters],
                "windows": [
                    _window_row(w, d) for w, d in zip(trial.results, diagnostics, strict=True)
                ],
            }
            consistent = all(d.consistent_with_engine for d in diagnostics)
            section.checks.append(
                check(
                    f"diagnostics.{candidate.candidate_id}",
                    "passed" if consistent else "failed",
                    "rule replay target changes equal engine fill counts"
                    if consistent
                    else "rule replay disagrees with engine fill counts",
                    category="formula",
                )
            )
    section.results = {
        "funding_events_in_coverage": len(rates),
        "funding_rate_min": min(rates) if rates else None,
        "funding_rate_max": max(rates) if rates else None,
        "runs": runs,
    }
    section.checks.append(
        check(
            "funding.provenance_and_coverage",
            "passed",
            "contract provenance, candle and funding coverage verified before any backtest",
            category="data_integrity",
        )
    )
    return section


# ---------------------------------------------------------------- running and reporting


def _sanitize(message: str) -> str:
    home = str(Path.home())
    return message.replace(home, "~").replace(home.replace("\\", "/"), "~")


def run_input_fingerprint(config_view: dict | None, inputs: list) -> str | None:
    """SHA-256 over what determines a run: effective config + logical input fingerprints.

    This identifies the INPUTS. `deterministic_sha256` identifies the OUTPUT
    payload; equal output hashes alone do not prove the inputs were the same.
    """
    if config_view is None:
        return None
    return fingerprint(
        {
            "config": config_view,
            "inputs": [[i["role"], i.get("logical_fingerprint_sha256")] for i in inputs],
        }
    )


def run_to_bundle(
    run_kind: str,
    output: Path,
    build: Callable[[OutputBundle], tuple[dict | None, str | None, Section]],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[Path, dict]:
    """Run `build` inside a staging bundle; always commit a report (failed or succeeded).

    If `build` raises, results are null and the error is recorded. If it
    returns a section carrying its own errors (e.g. one symbol of a smoke
    failed), its partial results are kept and the run is still "failed".
    """
    bundle = OutputBundle(output)
    created_at = clock()
    config_view, config_sha, errors = None, None, []
    section = Section()
    raised = False
    try:
        config_view, config_sha, section = build(bundle)
    except Exception as exc:  # noqa: BLE001 - recorded as a failed run, exit code 1
        raised = True
        errors.append(_sanitize(f"{type(exc).__name__}: {exc}"))
    errors += [_sanitize(e) for e in section.errors]
    deterministic = {
        "config": config_view,
        "config_sha256": config_sha,
        "run_input_sha256": run_input_fingerprint(config_view, section.inputs),
        "inputs": section.inputs,
        "checks": section.checks,
        "results": None if raised else section.results,
        "errors": errors,
        "limitations": section.limitations,
        "does_not_prove": section.does_not_prove,
    }
    report = build_report(run_kind=run_kind, deterministic=deterministic, created_at=created_at)
    bundle.write_report(report)
    return bundle.commit(), report


def _in_config_context(config: ResearchConfig, make_section: Callable[[ResearchConfig], Section]):
    """Run a section inside a FRESH context built from the config's decimal_context."""
    with localcontext(build_context(config.decimal_context)):
        return make_section(config)


def _config_builder(config_path: Path, make_section: Callable[[ResearchConfig], Section]):
    def build(bundle: OutputBundle):
        config = load_config(config_path)
        return config.effective(), config.sha256, _in_config_context(config, make_section)

    return build


def _offline_smoke_section(config: ResearchConfig) -> Section:
    from crypto_quant_lab.research import offline_fixture as fx

    inspect, basis, funding = (
        inspect_section(config),
        basis_section(config),
        funding_section(config),
    )
    section = Section(
        inputs=basis.inputs + [i for i in inspect.inputs if i["role"] == "funding"],
        checks=inspect.checks + basis.checks + funding.checks,
        results={"basis": basis.results, "funding_research": funding.results},
        limitations=[
            (
                "synthetic fixture data (research/offline_fixture.py); expectations are "
                "hand-derived in that module's docstring"
            )
        ]
        + basis.limitations
        + funding.limitations,
        does_not_prove=basis.does_not_prove
        + funding.does_not_prove
        + ["a passing offline smoke proves the pipeline wiring on synthetic data only"],
    )
    b = basis.results
    observed_basis = {
        "paired_observation_count": b["paired_observation_count"],
        "contract_only_open_times": b["contract_only_open_times"],
        "premium_count": b["premium_count"],
        "discount_count": b["discount_count"],
        "zero_count": b["zero_count"],
        "close_basis_min": b["close_basis"]["min"],
        "close_basis_max": b["close_basis"]["max"],
    }
    section.checks.append(
        check(
            "expectation.basis",
            "passed" if observed_basis == fx.EXPECTED_BASIS else "failed",
            f"observed {canonical_json(observed_basis)}",
            category="formula",
        )
    )
    runs = funding.results["runs"]
    for name, expected_windows in (
        ("carry_s2bp_l-1bp", fx.EXPECTED_CARRY_WINDOWS),
        ("no_trade_control", fx.EXPECTED_CONTROL_WINDOWS),
    ):
        observed = [
            {"fill_count": w["fill_count"], "final_equity": w["final_equity"]}
            for w in runs[name]["windows"]
        ]
        section.checks.append(
            check(
                f"expectation.{name}",
                "passed" if observed == list(expected_windows) else "failed",
                f"observed {canonical_json(observed)}",
                category="formula",
            )
        )
    return section


def offline_smoke_builder(bundle: OutputBundle):
    from crypto_quant_lab.research import offline_fixture as fx

    fixture = fx.build_offline_fixture(bundle.staging / "fixture")
    config_path = fixture.directory / "config.json"
    config_path.write_text(
        json.dumps(fixture.config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    config = load_config(config_path)
    return config.effective(), config.sha256, _in_config_context(config, _offline_smoke_section)


def to_view(value: dict) -> dict:
    return json.loads(canonical_json(value))


# ---------------------------------------------------------------- doctor


def doctor(config_path: Path, output: Path | None = None) -> list[tuple[str, str, str]]:
    """Read-only preflight; returns (status, name, detail) lines. Never writes anything."""
    lines: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        lines.append((status, name, detail))

    ok = sys.version_info[:2] == (3, 13)
    add("PASS" if ok else "FAIL", "python", f"{sys.version.split()[0]} (project requires 3.13)")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        add("FAIL", "config", f"{exc} — fix the field in {Path(config_path).name}")
        return lines
    add("PASS", "config", f"valid; sha256 {config.sha256}")
    add("PASS", "decimal_context", canonical_json(config.decimal_context))
    if output is not None:
        output = Path(output).resolve()
        if output.exists():
            add("FAIL", "output", f"{output.name} already exists; choose a new --output directory")
        elif output in config.stores.values() or any(
            output == p.parent or output in p.parents for p in config.stores.values()
        ):
            add("FAIL", "output", "output directory would contain or equal an input store")
        else:
            add("PASS", "output", "new directory; nothing will be overwritten")
    existing = {role: path for role, path in config.stores.items() if path.is_file()}
    for role, path in config.stores.items():
        if role not in existing:
            add(
                "FAIL",
                f"{role}.store",
                f"{path.name} does not exist (not created by doctor); "
                "ingest it first or fix stores." + role,
            )
    paths = list(existing.values())
    for i, first in enumerate(paths):
        for second in paths[i + 1 :]:
            if os.path.samefile(first, second):
                add("FAIL", "stores.separation", f"{first.name} and {second.name} are one file")
    expected = {role: config.expected_dataset(role) for role in ("contract", "index")}
    for role, path in existing.items():
        required = FUNDING_TABLES if role == "funding" else CANDLE_TABLES
        try:
            tables = sqlite_tables(path)
        except StorageError as exc:
            add("FAIL", f"{role}.schema", str(exc))
            continue
        missing = [t for t in required if t not in tables]
        if missing:
            add(
                "FAIL",
                f"{role}.schema",
                f"{path.name} lacks tables {missing}; not a "
                "provenance-aware store (doctor does not migrate)",
            )
            continue
        try:
            store = open_funding_store(path) if role == "funding" else open_candle_store(path)
        except (StorageError, DataCorruptionError) as exc:
            add("FAIL", f"{role}.schema", str(exc))
            continue
        add("PASS", f"{role}.schema", "required tables present and valid (read-only connection)")
        with read_stores(store):
            if role == "funding":
                if config.funding is not None:
                    report = build_funding_data_quality_report_from_store(
                        store,
                        exchange=BINANCE,
                        market_type=USDM_PERPETUAL,
                        symbol=config.symbol,
                        requested_start=config.funding.coverage_start,
                        requested_end=config.funding.coverage_end,
                    )
                    add(
                        "PASS" if report.overall_status == "PASS" else "FAIL",
                        "funding.coverage",
                        f"quality {report.overall_status}, {report.coverage_gap_count} gap(s) in "
                        "[funding_coverage_start, funding_coverage_end)",
                    )
                continue
            entry = candle_input(role, path, store, expected[role], config.start, config.end)
            for item in _candle_checks(
                role, entry, expected[role], store, config.start, config.end
            ):
                add("PASS" if item["status"] == "passed" else "FAIL", item["name"], item["detail"])
    return lines


# ---------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crypto_quant_lab.research",
        description="Offline research commands (network only with public-smoke --allow-network).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor", help="read-only preflight of a config and its stores")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, help="planned output directory to check")
    for name, text in (
        ("inspect", "dataset provenance, coverage and gaps"),
        ("basis-report", "descriptive close-basis report from the contract and index stores"),
        ("funding-research", "fixed-config funding research run with no-trade diagnostics"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--config", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True, help="NEW directory for the bundle")
    p = sub.add_parser("offline-smoke", help="synthetic end-to-end smoke in a new directory")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("public-smoke", help="OPT-IN: public Binance data, last 7 closed UTC days")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--allow-network",
        action="store_true",
        help="required: confirms public read-only HTTP requests",
    )
    p.add_argument(
        "--symbol",
        action="append",
        choices=PUBLIC_SMOKE_SYMBOLS,
        help="repeatable; default BTCUSDT",
    )
    p = sub.add_parser(
        "multileg-replay", help="config-driven multi-leg replay over existing stores (read-only)"
    )
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="NEW directory for the bundle")
    p = sub.add_parser(
        "multileg-example", help="write NEW synthetic stores plus an example multileg config"
    )
    p.add_argument("--output", type=Path, required=True, help="NEW directory for the example")
    return parser


def _multileg_replay_builder(config_path: Path):
    from crypto_quant_lab.research.multileg_config import multileg_replay_builder

    return multileg_replay_builder(config_path)


def _multileg_example(output: Path) -> int:
    from crypto_quant_lab.research.multileg_config import write_example

    try:
        config_path = write_example(output)
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"example written (SYNTHETIC stores, closed writers): {config_path}")
    return 0


def _now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        lines = doctor(args.config, args.output)
        for status, name, detail in lines:
            print(f"[{status}] {name}: {detail}")
        return 1 if any(status == "FAIL" for status, _, _ in lines) else 0
    if args.command == "public-smoke" and not args.allow_network:
        print(
            "public-smoke uses the network; re-run with --allow-network to confirm", file=sys.stderr
        )
        return 2
    if args.command == "public-smoke":
        from crypto_quant_lab.research.public_smoke import public_smoke_builder
    if args.command == "multileg-example":
        return _multileg_example(args.output)

    builders = {
        "inspect": lambda: _config_builder(args.config, inspect_section),
        "basis-report": lambda: _config_builder(args.config, basis_section),
        "funding-research": lambda: _config_builder(args.config, funding_section),
        "offline-smoke": lambda: offline_smoke_builder,
        "public-smoke": lambda: public_smoke_builder(tuple(args.symbol or ["BTCUSDT"]), _now),
        "multileg-replay": lambda: _multileg_replay_builder(args.config),
    }
    try:
        path, report = run_to_bundle(args.command, args.output, builders[args.command]())
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    warnings = report["warning_count"]
    suffix = f" with {warnings} warning(s) — read them" if warnings else ""
    print(f"{report['status']}{suffix}: {path}")
    for item in report["deterministic"]["checks"]:
        print(f"  [{item['status']}] ({item['category']}) {item['name']}: {item['detail']}")
    for error in report["deterministic"]["errors"]:
        print(f"  error: {error}")
    return 0 if report["status"] == "succeeded" else 1
