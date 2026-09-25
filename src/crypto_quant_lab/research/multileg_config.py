"""Config-driven multi-leg research run over EXISTING stores (FUNDING_RESEARCH_SPEC.md Bölüm 19.15).

    python -m crypto_quant_lab.research multileg-example --output <NEW directory>
    python -m crypto_quant_lab.research multileg-replay --config <file> --output <NEW directory>
    python -m crypto_quant_lab.research multileg-doctor --config <file> --output <NEW directory>

`multileg-replay` reads ONE strict JSON config, builds a
`StoreBackedMultiLegRequest` from it and hands it to the existing read-only
store runner (`research/multileg_store.py`: open_read_only -> provenance /
coverage / grid / funding checks -> unchanged `run_multileg_replay`). No
accounting, funding, cost or timing logic lives here, and no economic value
is defaulted: pair, window, stores, source labels, wallets, cost models,
funding model and the intent script must all be written in the config. Only
`decimal_context` may be omitted (the documented default of
research/decimal_policy.py, recorded resolved in the report).

Config rules (a violation is a ConfigError naming the field, raised before
any store is opened): duplicate JSON keys, unknown fields, JSON numbers with
a fraction/exponent, NaN/Infinity, null or booleans where a string/number is
expected, non-decimal strings, naive timestamps and unsupported models are
refused. Store paths are relative to the config file (never to the cwd).

`multileg-doctor` (optional, never required by `multileg-replay`) parses the
same config and runs the same read-only store preparation
(`multileg_store.prepare_store_backed_inputs`) WITHOUT the replay or the
accounting; solvency and the replay's own checks are reported NOT_EVALUATED.

`multileg-example` writes NEW synthetic stores through the real writers
(research/multileg_store_demo.py) plus an example config next to them; it
is only a starting point — the replay command never uses built-in scenarios.

SCRIPTED INTENTS · NOT A STRATEGY · no liquidation, legging, borrow,
lot/tick or wallet-transfer model (see the report's limitations).
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

from crypto_quant_lab.backtest.costs import (
    CompositeCostModel,
    CostModel,
    ProportionalCommissionModel,
    ProportionalSlippageCostModel,
    ProportionalSpreadCostModel,
    ZeroCostModel,
)
from crypto_quant_lab.backtest.multileg import HedgedPair, TradableInstrument
from crypto_quant_lab.backtest.multileg_replay import HedgeAction, HedgeIntent
from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.research import multileg_store
from crypto_quant_lab.research import multileg_store_demo as demo
from crypto_quant_lab.research.cli import (
    ConfigError,
    Section,
    _decimal,
    _field,
    _reject_constant,
    _reject_float,
    _time,
)
from crypto_quant_lab.research.decimal_policy import build_context, normalize_decimal_context
from crypto_quant_lab.research.multileg_offline import result_view
from crypto_quant_lab.research.report import canonical_json, check, fingerprint, sha256_hex

CONFIG_KIND = "multileg_replay"
CONFIG_VERSION = 1
EXAMPLE_CONFIG_NAME = "config.json"
INPUT_IDENTITY_SCHEME = "multileg-input/v1"
_STORE_ROLES = ("spot", "perpetual", "funding")
_SOURCE_ROLES = ("spot", "perpetual")
_COST_FIELDS = {
    "zero": (),
    "proportional_commission": ("rate",),
    "proportional_spread": ("half_spread_rate",),
    "proportional_slippage": ("rate",),
    "composite": ("components",),
}
IDENTITY_SCOPE = {
    "config_sha256": (
        "SHA-256 of the canonical JSON of the PARSED config (key order, whitespace and a UTF-8 "
        "BOM do not change it; not a raw-byte file hash); NOT the run identity"
    ),
    "replay_input_sha256": (
        "consumed candle and funding values, window, as_of, intents, wallets, cost and funding "
        "model parameters and the Decimal context (runner-defined); source labels excluded"
    ),
    "run_input_sha256": (
        "effective config (store file names, declared sources, resolved Decimal context) plus "
        "each input's logical fingerprint, including replay_input_sha256"
    ),
    "multileg_input_sha256": (
        "scheme multileg-input/v1: replay_input_sha256 + config kind/version + per leg the "
        "registered dataset identity (source included), coverage, candle count, consumed and "
        "store-provenance fingerprints + funding partition, coverage quality, gap/event counts "
        "and consumed fingerprint (the funding store records no source); None when "
        "replay_input_sha256 is None"
    ),
    "deterministic_sha256": "the deterministic OUTPUT payload of this report",
}


@dataclass(frozen=True, slots=True)
class MultilegConfig:
    raw: dict
    sha256: str
    stores: dict[str, Path]
    decimal_context: dict
    request: multileg_store.StoreBackedMultiLegRequest

    def effective(self) -> dict:
        """The config as recorded in reports: store file names only, resolved Decimal context."""
        recorded = json.loads(json.dumps(self.raw))
        recorded["stores"] = {role: path.name for role, path in self.stores.items()}
        recorded["decimal_context"] = self.decimal_context
        return recorded


# ---------------------------------------------------------------- strict parsing


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return {int: "number", str: "string", list: "array", dict: "object"}.get(
        type(value), type(value).__name__
    )


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    mapping: dict = {}
    for key, value in pairs:
        if key in mapping:
            raise ConfigError(f"duplicate JSON key {key!r}")
        mapping[key] = value
    return mapping


def _object(value: object, where: str, required: set[str], optional: frozenset = frozenset()):
    label = where.rstrip(".") or "config"
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object, got {_json_kind(value)}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        hint = ""
        if unknown[0] == "warmup":
            hint = " (warmup is not supported: the replay evaluates every candle of the window)"
        raise ConfigError(f"{where}{unknown[0]}: unknown field{hint}")
    missing = sorted(required - set(value))
    if missing:
        raise ConfigError(f"missing config field {where}{missing[0]}")
    return value


def _string(mapping: dict, key: str, where: str) -> str:
    value = _field(mapping, key, where)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}{key} must be a non-empty string, got {_json_kind(value)}")
    return value


def _non_negative(mapping: dict, key: str, where: str, *, positive: bool = False) -> Decimal:
    value = _decimal(mapping, key, where)
    if positive and value <= 0:
        raise ConfigError(f"{where}{key} must be > 0, got {value}")
    if value < 0:
        raise ConfigError(f"{where}{key} must be >= 0, got {value}")
    return value


def _cost_model(raw: object, where: str, *, nested: bool = False) -> CostModel:
    if not isinstance(raw, dict):
        raise ConfigError(f'{where} must be an object like {{"model": "zero"}}')
    name = _string(raw, "model", f"{where}.")
    if name not in _COST_FIELDS or (nested and name == "composite"):
        supported = [n for n in _COST_FIELDS if not (nested and n == "composite")]
        raise ConfigError(
            f"{where}.model: unsupported cost model {name!r} (supported: {supported})"
        )
    _object(raw, f"{where}.", {"model", *_COST_FIELDS[name]})
    try:
        if name == "zero":
            return ZeroCostModel()
        if name == "proportional_commission":
            return ProportionalCommissionModel(rate=_decimal(raw, "rate", f"{where}."))
        if name == "proportional_spread":
            return ProportionalSpreadCostModel(
                half_spread_rate=_decimal(raw, "half_spread_rate", f"{where}.")
            )
        if name == "proportional_slippage":
            return ProportionalSlippageCostModel(rate=_decimal(raw, "rate", f"{where}."))
        components = raw["components"]
        if not isinstance(components, list):
            raise ConfigError(f"{where}.components must be an array")
        return CompositeCostModel(
            components=tuple(
                _cost_model(c, f"{where}.components[{i}]", nested=True)
                for i, c in enumerate(components)
            )
        )
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def _intents(raw: object, run_start: datetime, run_end: datetime, timeframe: str):
    if not isinstance(raw, list):
        raise ConfigError(f"intents must be an array, got {_json_kind(raw)}")
    intents = []
    for index, item in enumerate(raw):
        where = f"intents[{index}]."
        _object(item, where, {"action", "decision_time", "quantity"})
        action = _string(item, "action", where)
        if action not in ("OPEN", "CLOSE"):
            raise ConfigError(f'{where}action must be "OPEN" or "CLOSE", got {action!r}')
        time = _time(item, "decision_time", where)
        if not (run_start < time <= run_end and is_grid_aligned(time, timeframe)):
            raise ConfigError(
                f"{where}decision_time must be a candle availability instant "
                f"(open_time + {timeframe}) inside (run_start, run_end]"
            )
        if intents and time <= intents[-1].decision_time:
            raise ConfigError(f"{where}decision_time must be strictly later than the previous one")
        quantity = _non_negative(item, "quantity", where, positive=True)
        intents.append(HedgeIntent(HedgeAction(action), time, quantity))
    actions = [i.action.value for i in intents]
    if actions not in ([], ["OPEN"], ["OPEN", "CLOSE"]):
        raise ConfigError(
            f"intents must be [], [OPEN] or [OPEN, CLOSE] (one lifecycle, no reversal), got "
            f"{actions}"
        )
    if len(intents) == 2 and intents[0].quantity != intents[1].quantity:
        raise ConfigError("intents[1].quantity must equal the OPEN quantity (no partial close)")
    return tuple(intents)


def parse_multileg_config(raw: object, *, base_dir: Path) -> MultilegConfig:
    required = {
        "config_kind", "config_version", "pair", "timeframe", "run_start", "run_end", "as_of",
        "stores", "sources", "wallets", "costs", "funding_model", "intents",
    }  # fmt: skip
    _object(raw, "", required, frozenset({"decimal_context"}))
    if raw["config_kind"] != CONFIG_KIND:
        raise ConfigError(f'config_kind must be "{CONFIG_KIND}"')
    version = raw["config_version"]
    if type(version) is not int or version != CONFIG_VERSION:
        raise ConfigError(f"config_version must be the integer {CONFIG_VERSION}")

    pair_raw = _object(
        raw["pair"],
        "pair.",
        {"pair_id", "exchange", "spot_symbol", "perpetual_symbol", "quote_asset"},
    )
    names = {key: _string(pair_raw, key, "pair.") for key in pair_raw}
    try:
        pair = HedgedPair(
            names["pair_id"],
            TradableInstrument(
                names["exchange"], "spot", names["spot_symbol"], names["quote_asset"]
            ),
            TradableInstrument(
                names["exchange"], "usdm_perpetual", names["perpetual_symbol"], names["quote_asset"]
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"pair: {exc}") from exc

    timeframe = _string(raw, "timeframe", "")
    try:
        candle_duration(timeframe)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"timeframe: {exc}") from exc
    run_start, run_end, as_of = _time(raw, "run_start"), _time(raw, "run_end"), _time(raw, "as_of")
    if not run_start < run_end:
        raise ConfigError("run_start must be before run_end")
    if not (is_grid_aligned(run_start, timeframe) and is_grid_aligned(run_end, timeframe)):
        raise ConfigError(f"run_start/run_end must be aligned to the {timeframe!r} UTC grid")
    if as_of < run_end:
        raise ConfigError("as_of must be >= run_end (the window must be closed as of as_of)")

    stores_raw = _object(raw["stores"], "stores.", set(_STORE_ROLES))
    stores = {
        role: (base_dir / _string(stores_raw, role, "stores.")).resolve() for role in _STORE_ROLES
    }
    if len(set(stores.values())) != len(stores):
        raise ConfigError("stores: two roles point to the same file (spot, perpetual, funding)")
    existing = [path for path in stores.values() if path.is_file()]
    for i, first in enumerate(existing):
        for second in existing[i + 1 :]:
            if first.samefile(second):
                raise ConfigError("stores: two roles point to the same physical file (alias/link)")

    sources_raw = raw["sources"]
    if isinstance(sources_raw, dict) and "funding" in sources_raw:
        raise ConfigError(
            "sources.funding: the funding store records no source label; funding evidence "
            "is its partition and coverage only"
        )
    _object(sources_raw, "sources.", set(_SOURCE_ROLES))
    sources = {role: _string(sources_raw, role, "sources.") for role in _SOURCE_ROLES}

    wallets_raw = _object(raw["wallets"], "wallets.", {"spot_cash", "perpetual_collateral"})
    costs_raw = _object(raw["costs"], "costs.", {"spot", "perpetual"})
    funding_raw = _object(raw["funding_model"], "funding_model.", {"model"})
    if _string(funding_raw, "model", "funding_model.") != "linear":
        raise ConfigError(
            f"funding_model.model: unsupported funding model {funding_raw['model']!r} "
            '(supported: ["linear"])'
        )
    try:
        decimal_context = normalize_decimal_context(raw.get("decimal_context"))
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc

    request = multileg_store.StoreBackedMultiLegRequest(
        pair=pair,
        timeframe=timeframe,
        run_start=run_start,
        run_end=run_end,
        as_of_time=as_of,
        spot=multileg_store.CandleStoreSource(stores["spot"], sources["spot"]),
        perpetual=multileg_store.CandleStoreSource(stores["perpetual"], sources["perpetual"]),
        funding_path=stores["funding"],
        intents=_intents(raw["intents"], run_start, run_end, timeframe),
        spot_cash=_non_negative(wallets_raw, "spot_cash", "wallets."),
        perpetual_collateral=_non_negative(wallets_raw, "perpetual_collateral", "wallets."),
        spot_cost_model=_cost_model(costs_raw["spot"], "costs.spot"),
        perpetual_cost_model=_cost_model(costs_raw["perpetual"], "costs.perpetual"),
        funding_model=LinearFundingModel(),
        decimal_context=decimal_context,
    )
    return MultilegConfig(
        raw=raw,
        sha256=sha256_hex(canonical_json(raw)),
        stores=stores,
        decimal_context=decimal_context,
        request=request,
    )


def load_multileg_config(path: Path) -> MultilegConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path.name}")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}") from exc
    return parse_multileg_config(raw, base_dir=path.resolve().parent)


# ---------------------------------------------------------------- shared evidence / identity


def _require_store_files(config: MultilegConfig) -> None:
    for role, path in config.stores.items():
        if not path.is_file():
            raise ConfigError(
                f"stores.{role}: file not found: {path.name} (read-only runs never create stores)"
            )


def _candle_input(evidence: multileg_store.CandleEvidence) -> dict:
    d = evidence.dataset
    return {
        "role": evidence.role,
        "file_name": evidence.file_name,
        "registered_dataset": {
            "exchange": d.exchange,
            "market_type": d.market_type,
            "symbol": d.symbol,
            "timeframe": d.timeframe,
            "price_kind": d.price_kind,
            "source": d.source,
        },
        "coverage": [[c.start_time, c.end_time] for c in evidence.coverage],
        "candle_count": evidence.candle_count,
        "logical_fingerprint_sha256": evidence.consumed_sha256,
        "store_provenance_sha256": evidence.store_provenance_sha256,
    }


def _funding_input(evidence: multileg_store.FundingEvidence) -> dict:
    return {
        "role": "funding",
        "file_name": evidence.file_name,
        "partition": list(evidence.partition),
        "quality_status": evidence.quality_status,
        "coverage_gap_count": evidence.coverage_gap_count,
        "event_count": evidence.event_count,
        "source_recorded": evidence.source_recorded,
        "logical_fingerprint_sha256": evidence.consumed_sha256,
    }


def _report_inputs(spot, perpetual, funding, replay_input: str | None) -> list:
    return [
        _candle_input(spot),
        _candle_input(perpetual),
        _funding_input(funding),
        {"role": "replay_input", "logical_fingerprint_sha256": replay_input},
    ]


def _input_evidence(config: MultilegConfig, spot, perpetual, funding) -> dict:
    """Readable input summary (rendered in report.md through `results`)."""
    request = config.request
    return {
        "requested": {
            "timeframe": request.timeframe,
            "run_start": request.run_start,
            "run_end": request.run_end,
            "as_of": request.as_of_time,
        },
        "declared_sources": {
            "spot": request.spot.expected_source,
            "perpetual": request.perpetual.expected_source,
        },
        "spot": _candle_input(spot),
        "perpetual": _candle_input(perpetual),
        "funding": _funding_input(funding),
    }


def multileg_input_fingerprint(spot, perpetual, funding, replay_input: str | None) -> str | None:
    """INPUT_IDENTITY_SCHEME identity; None when the replay input itself is unidentifiable."""
    if replay_input is None:
        return None
    return fingerprint(
        {
            "scheme": INPUT_IDENTITY_SCHEME,
            "config": [CONFIG_KIND, CONFIG_VERSION],
            "replay_input_sha256": replay_input,
            "legs": [
                {
                    key: value
                    for key, value in _candle_input(leg).items()
                    if key not in ("file_name", "role")
                }
                | {"role": leg.role}
                for leg in (spot, perpetual)
            ],
            "funding": {
                key: value for key, value in _funding_input(funding).items() if key != "file_name"
            },
        }
    )


def _provenance_check(spot, perpetual, funding) -> dict:
    return check(
        "inputs.provenance_and_coverage",
        "passed",
        f"spot/perpetual provenance equal the declared sources "
        f"({spot.dataset.source}, {perpetual.dataset.source}); coverage contains "
        f"[run_start, run_end) and every grid slot holds a candle; funding coverage "
        f"{funding.quality_status} for partition {list(funding.partition)} "
        "(the funding store records no source)",
        category="data_integrity",
    )


def _identity_check(replay_input: str | None) -> dict:
    return check(
        "identity.replay_input",
        "passed" if replay_input else "failed",
        "replay input fingerprint computed"
        if replay_input
        else "the runner could not identify the replay inputs; the run is not reproducible",
        category="execution",
    )


_COMMON_LIMITATIONS = [
    multileg_store.SNAPSHOT_SCOPE,
    (
        "source labels are the writers' declarations, compared exactly; they are not "
        "proof of endpoint access; the funding store records no source"
    ),
]


# ---------------------------------------------------------------- run


def _no_trade_explanation(run: multileg_store.StoreBackedMultiLegRun) -> str | None:
    result = run.result
    if result.paired_fills:
        return None
    if not run.request.intents:
        return (
            "the config scripts no intents: the pair stays FLAT for the whole window; zero "
            "trades is the valid outcome of this script, not a data error (the replay ran)"
        )
    return (
        "every scripted intent fell on the last candle's availability instant; with no next "
        "candle it is reported as unexecuted and never filled"
    )


def multileg_section(config: MultilegConfig) -> Section:
    """Run the store-backed replay for one parsed config (inside the caller's Decimal context)."""
    _require_store_files(config)
    run = multileg_store.run_store_backed_multileg_replay(config.request)
    result = run.result
    initial = result.initial_state
    results = result_view(result) | {
        "initial_wallets": {
            "spot_cash": initial.spot.cash,
            "perpetual_collateral": initial.perpetual.collateral,
        },
        "intent_count": len(config.request.intents),
        "paired_fill_count": len(result.paired_fills),
        "no_trade_explanation": _no_trade_explanation(run),
        "identity_scope": IDENTITY_SCOPE,
        "resolved_config": config.effective(),
        "input_evidence": _input_evidence(config, run.spot, run.perpetual, run.funding),
        "multileg_input_sha256": multileg_input_fingerprint(
            run.spot, run.perpetual, run.funding, run.replay_input_sha256
        ),
    }
    unexecuted = result.unexecuted_intents
    if unexecuted:
        intents_detail = f"{len(unexecuted)} intent(s) NOT executed: " + "; ".join(
            f"{u.intent.action.value} at {u.intent.decision_time.isoformat()}: {u.reason}"
            for u in unexecuted
        )
    else:
        intents_detail = (
            f"{len(result.paired_fills)} paired fill(s) for "
            f"{len(config.request.intents)} scripted intent(s)"
        )
    if result.position_open_at_end:
        position_detail = (
            "position still OPEN at run end: final equity includes unrealized PnL valued at "
            "the last trade CLOSEs; no closing cost, no synthetic close, no liquidation model"
        )
    else:
        position_detail = f"lifecycle at end: {result.final_state.lifecycle.value}"
    checks = [
        _provenance_check(run.spot, run.perpetual, run.funding),
        _identity_check(run.replay_input_sha256),
        check(
            "intents.execution",
            "warning" if unexecuted else "passed",
            intents_detail,
            category="execution",
        ),
        check(
            "position.at_end",
            "warning" if result.position_open_at_end else "passed",
            position_detail,
            category="execution",
        ),
    ]
    return Section(
        results=results,
        checks=checks,
        inputs=_report_inputs(run.spot, run.perpetual, run.funding, run.replay_input_sha256),
        limitations=[
            "SCRIPTED intents from the config; NOT A STRATEGY; no signal or opportunity selection",
            *_COMMON_LIMITATIONS,
            (
                "valuation at trade CLOSE (no exchange mark-price series); hedge ratio 1:1; no "
                "warmup; no liquidation, margin, legging, partial fill, borrow, lot/tick rules "
                "or wallet transfers are modelled"
            ),
        ],
        does_not_prove=[
            "profitability or real-exchange feasibility of any basis/carry trade",
            "that the scripted intents were written without hindsight",
            "a zero-trade result is a property of this script, not a risk or no-trade decision",
        ],
    )


def multileg_replay_builder(config_path: Path):
    def build(bundle):
        config = load_multileg_config(config_path)
        with localcontext(build_context(config.decimal_context)):
            section = multileg_section(config)
        return config.effective(), config.sha256, section

    return build


# ---------------------------------------------------------------- doctor (no replay)

NOT_EVALUATED = {
    "economics.solvency": (
        "whether the wallets can pay the fills (e.g. insufficient spot cash) is decided only by "
        "the accounting inside a replay"
    ),
    "replay.core_validation": (
        "run_multileg_replay's own checks (leg/instrument match, identical open_time grids, "
        "funding event identity/order/duplicates and settlement, intents against candle "
        "availability) run only inside a replay"
    ),
    "economics.results": (
        "fills, marks, funding cashflows, realized/unrealized PnL and final equity: no replay ran"
    ),
}
DOCTOR_SCOPE = (
    "multileg-doctor parses the config with the multileg-replay parser and runs the SAME "
    "read-only store preparation as multileg-replay (request checks, provenance, coverage, "
    "grid, funding coverage) at this read time; it never calls the replay or the accounting"
)


def multileg_doctor_section(config: MultilegConfig) -> Section:
    """Replay-free check of one parsed config (inside the caller's Decimal context)."""
    _require_store_files(config)
    prepared = multileg_store.prepare_store_backed_inputs(config.request)
    spot, perpetual = prepared.spot_evidence, prepared.perpetual_evidence
    funding = prepared.funding_evidence
    replay_input = multileg_store.replay_input_fingerprint(
        config.request,
        prepared.decimal_context,
        prepared.spot,
        prepared.perpetual,
        prepared.funding_events,
    )
    checks = [
        check(
            "config.parse",
            "passed",
            "config accepted by the multileg-replay parser",
            category="data_integrity",
        ),
        _provenance_check(spot, perpetual, funding),
        _identity_check(replay_input),
        *(
            check(name, "skipped", f"NOT_EVALUATED: {detail}", category="execution")
            for name, detail in NOT_EVALUATED.items()
        ),
    ]
    results = {
        "replay_executed": False,
        "doctor_scope": DOCTOR_SCOPE,
        "not_evaluated": NOT_EVALUATED,
        "resolved_config": config.effective(),
        "input_evidence": _input_evidence(config, spot, perpetual, funding),
        "replay_input_sha256": replay_input,
        "multileg_input_sha256": multileg_input_fingerprint(spot, perpetual, funding, replay_input),
        "identity_scope": IDENTITY_SCOPE
        | {
            "doctor_note": (
                "the input identities describe what a replay WOULD consume at this read; they do "
                "not mean a replay ran. A later multileg-replay re-reads and re-validates every "
                "input and never reads this report"
            ),
            "deterministic_sha256": "the deterministic OUTPUT payload of this doctor report",
        },
    }
    return Section(
        results=results,
        checks=checks,
        inputs=_report_inputs(spot, perpetual, funding, replay_input),
        limitations=[
            (
                "a doctor PASS covers only the listed checks at this read time; NOT_EVALUATED "
                "items were not checked"
            ),
            *_COMMON_LIMITATIONS,
        ],
        does_not_prove=[
            "that a replay will succeed (solvency and core replay validation are NOT_EVALUATED)",
            "that the same data will exist or be unchanged when a replay runs",
            "profitability or real-exchange feasibility of any basis/carry trade",
        ],
    )


def multileg_doctor_builder(config_path: Path):
    def build(bundle):
        config = load_multileg_config(config_path)
        with localcontext(build_context(config.decimal_context)):
            section = multileg_doctor_section(config)
        return config.effective(), config.sha256, section

    return build


# ---------------------------------------------------------------- example


def example_config() -> dict:
    """The example for `multileg-example` stores (the demo's proportional-cost scenario)."""
    return {
        "config_kind": CONFIG_KIND,
        "config_version": CONFIG_VERSION,
        "pair": {
            "pair_id": demo.PAIR.pair_id,
            "exchange": demo.PAIR.spot.exchange,
            "spot_symbol": demo.PAIR.spot.symbol,
            "perpetual_symbol": demo.PAIR.perpetual.symbol,
            "quote_asset": demo.PAIR.spot.quote_asset,
        },
        "timeframe": "1h",
        "run_start": "2026-01-01T00:00:00Z",
        "run_end": "2026-01-01T04:00:00Z",
        "as_of": "2026-01-01T04:00:00Z",
        "stores": {"spot": "spot.db", "perpetual": "perpetual.db", "funding": "funding_t3.db"},
        "sources": {"spot": demo.SPOT_SOURCE, "perpetual": demo.PERP_SOURCE},
        "wallets": {"spot_cash": "200", "perpetual_collateral": "200"},
        "costs": {
            "spot": {"model": "proportional_commission", "rate": "0.001"},
            "perpetual": {"model": "proportional_commission", "rate": "0.0005"},
        },
        "funding_model": {"model": "linear"},
        "intents": [
            {"action": "OPEN", "decision_time": "2026-01-01T01:00:00Z", "quantity": "1"},
            {"action": "CLOSE", "decision_time": "2026-01-01T03:00:00Z", "quantity": "1"},
        ],
    }


class ExampleWriteError(RuntimeError):
    """Writing the example failed after the output step (not an --output usage error)."""

    def __init__(self, directory: Path, cause: BaseException) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.directory = directory


def _is_output_error(exc: OSError, directory: Path) -> bool:
    """True only for the creation of `directory` itself (exists / parent missing)."""
    return exc.filename is not None and Path(exc.filename) == directory


def write_example(directory: Path) -> Path:
    """NEW directory: synthetic stores (real writers, closed) + `config.json` next to them.

    FileExistsError / FileNotFoundError about `directory` itself (the `--output`
    step) propagate unchanged; any failure after that is an ExampleWriteError.
    The directory is never deleted or repaired; `config.json` is written last.
    """
    directory = Path(directory)
    try:
        demo.build_store_fixture(directory)  # its first action creates `directory`
        path = directory / EXAMPLE_CONFIG_NAME
        temporary = directory / f".{EXAMPLE_CONFIG_NAME}.tmp"
        temporary.write_text(json.dumps(example_config(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)  # the config appears only after every store was written
    except (FileExistsError, FileNotFoundError) as exc:
        if _is_output_error(exc, directory):
            raise
        raise ExampleWriteError(directory, exc) from exc
    except Exception as exc:
        raise ExampleWriteError(directory, exc) from exc
    return path
