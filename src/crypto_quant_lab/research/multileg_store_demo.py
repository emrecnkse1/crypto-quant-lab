"""Store-backed multi-leg replay demo on NEW synthetic stores (FUNDING_RESEARCH_SPEC.md Bölüm 19.13).

    python -m crypto_quant_lab.research.multileg_store_demo --output <NEW directory>

SYNTHETIC DATA · SCRIPTED INTENTS · NOT A STRATEGY · NO REAL MARKET DATA.
1. Writes fresh synthetic stores into `<output>/fixture/` through real writers:
   spot candles via `ingest_binance_spot_klines_with_provenance` over a fake
   transport (source label "synthetic:..."), perpetual candles via the candle
   store's `write_ingestion_batch` (written before Bölüm 19.14, when the
   USDⓈ-M ingestion function could only record the Binance endpoint; kept
   unchanged since it declares a synthetic label directly), and
   settled funding via the funding store's `write_ingestion_batch`. All
   writers are closed before step 2.
2. Re-opens those stores read-only through `run_store_backed_multileg_replay`
   (reader -> validation -> replay) for the four `multileg_offline` scenarios
   (same synthetic market, same hand-derived expectations).
3. Writes a report v2 bundle; the fixture stores stay inside the bundle.
No network, no existing database, nothing overwritten.
"""

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from crypto_quant_lab.backtest.multileg_replay import HedgeAction, HedgeIntent
from crypto_quant_lab.data_quality.ingestion import ingest_binance_spot_klines_with_provenance
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.binance_historical import parse_binance_historical_kline
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research import multileg_offline as offline
from crypto_quant_lab.research.decimal_policy import normalize_decimal_context
from crypto_quant_lab.research.multileg_store import (
    CandleStoreSource,
    StoreBackedMultiLegRequest,
    StoreBackedMultiLegRun,
    describe_cost_model,
    describe_funding_model,
    run_store_backed_multileg_replay,
)
from crypto_quant_lab.research.report import (
    OutputBundle,
    build_report,
    canonical_json,
    check,
    fingerprint,
)
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import CONTRACT_TRADE, CandleDataset
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

FIXTURE_VERSION = "multileg-store-demo/v1"
SPOT_SOURCE = "synthetic:multileg-store-demo/spot/v1"
PERP_SOURCE = "synthetic:multileg-store-demo/perpetual/v1"
T0 = offline._T0
H = offline._H
T4 = T0 + 4 * H
PAIR = offline._PAIR
SYMBOL = "SYNTHUSDT"
_T0_MS = 1767225600000  # 2026-01-01T00:00:00Z
_H_MS = 3_600_000


def _spot_page(*, start_time_ms, end_time_ms):
    rows = []
    for k, (open_, close) in enumerate(offline._SPOT_ROWS):
        open_ms = _T0_MS + k * _H_MS
        high = max(open_, close, key=Decimal)
        low = min(open_, close, key=Decimal)
        rows.append(
            [open_ms, open_, high, low, close, "1", open_ms + _H_MS - 1, "100", 1, "0.5", "50", "0"]
        )
    return [
        parse_binance_historical_kline(r, SYMBOL, "1h")
        for r in rows
        if start_time_ms <= r[0] <= end_time_ms
    ]


def build_store_fixture(directory: Path) -> dict[str, Path]:
    """Create the synthetic stores in a NEW directory; returns the store paths."""
    directory = Path(directory)
    directory.mkdir(parents=False, exist_ok=False)
    paths = {
        "spot": directory / "spot.db",
        "perpetual": directory / "perpetual.db",
        "funding_none": directory / "funding_none.db",
        "funding_t3": directory / "funding_t3.db",
    }
    spot = SQLiteHistoricalCandleStore(paths["spot"])
    try:
        ingest_binance_spot_klines_with_provenance(
            spot,
            symbol=SYMBOL,
            timeframe="1h",
            requested_start=T0,
            requested_end=T4,
            as_of_time=T4,
            fetch_page=_spot_page,
            source=SPOT_SOURCE,
        )
    finally:
        spot.close()
    perp = SQLiteHistoricalCandleStore(paths["perpetual"])
    try:
        dataset = CandleDataset(
            "binance", "usdm_perpetual", SYMBOL, "1h", CONTRACT_TRADE, PERP_SOURCE
        )
        records = [
            HistoricalCandle(
                exchange="binance",
                market_type="usdm_perpetual",
                candle=Candle(
                    SYMBOL,
                    "1h",
                    T0 + k * H,
                    Decimal(o),
                    max(Decimal(o), Decimal(c)),
                    min(Decimal(o), Decimal(c)),
                    Decimal(c),
                    Decimal(1),
                ),
            )
            for k, (o, c) in enumerate(offline._PERP_ROWS)
        ]
        perp.write_ingestion_batch(records, dataset=dataset, covered_start=T0, covered_end=T4)
    finally:
        perp.close()
    for key, events in (
        ("funding_none", []),
        (
            "funding_t3",
            [
                HistoricalFundingEvent(
                    exchange="binance",
                    market_type="usdm_perpetual",
                    symbol=SYMBOL,
                    funding=FundingEvent(
                        event_time=T0 + 3 * H,
                        funding_rate=Decimal("0.0001"),
                        reference_price=Decimal(101),
                        rate_type="Regular",
                    ),
                )
            ],
        ),
    ):
        store = SQLiteHistoricalFundingStore(paths[key])
        try:
            store.write_ingestion_batch(
                events,
                exchange="binance",
                market_type="usdm_perpetual",
                symbol=SYMBOL,
                covered_start=T0,
                covered_end=T4,
            )
        finally:
            store.close()
    return paths


def scenario_request(scenario, paths: dict[str, Path]) -> StoreBackedMultiLegRequest:
    intents = [HedgeIntent(HedgeAction.OPEN, T0 + H, Decimal(1))]
    if scenario.close:
        intents.append(HedgeIntent(HedgeAction.CLOSE, T0 + 3 * H, Decimal(1)))
    return StoreBackedMultiLegRequest(
        pair=PAIR,
        timeframe="1h",
        run_start=T0,
        run_end=T4,
        as_of_time=T4,
        spot=CandleStoreSource(paths["spot"], SPOT_SOURCE),
        perpetual=CandleStoreSource(paths["perpetual"], PERP_SOURCE),
        funding_path=paths["funding_t3" if scenario.fundings else "funding_none"],
        intents=tuple(intents),
        spot_cash=Decimal(200),
        perpetual_collateral=Decimal(200),
        spot_cost_model=offline._cost(scenario.spot_fee_rate),
        perpetual_cost_model=offline._cost(scenario.perp_fee_rate),
        funding_model=LinearFundingModel(),
        decimal_context=normalize_decimal_context(None),
    )


def run_view(run: StoreBackedMultiLegRun) -> dict:
    view = offline.result_view(run.result)
    view["evidence"] = {
        leg.role: {
            "file_name": leg.file_name,
            "dataset": [
                leg.dataset.exchange,
                leg.dataset.market_type,
                leg.dataset.symbol,
                leg.dataset.timeframe,
                leg.dataset.price_kind,
                leg.dataset.source,
            ],
            "coverage": [[c.start_time, c.end_time] for c in leg.coverage],
            "candle_count": leg.candle_count,
            "consumed_sha256": leg.consumed_sha256,
            "store_provenance_sha256": leg.store_provenance_sha256,
        }
        for leg in (run.spot, run.perpetual)
    } | {
        "funding": {
            "file_name": run.funding.file_name,
            "partition": list(run.funding.partition),
            "quality_status": run.funding.quality_status,
            "event_count": run.funding.event_count,
            "consumed_sha256": run.funding.consumed_sha256,
            "source_recorded": run.funding.source_recorded,
        }
    }
    view["replay_input_sha256"] = run.replay_input_sha256
    view["snapshot_scope"] = run.snapshot_scope
    return view


def build_deterministic(fixture_dir: Path, scenarios=offline.SCENARIOS) -> dict:
    paths = build_store_fixture(fixture_dir)
    results, checks, errors = {}, [], []
    configs = []
    for scenario in scenarios:
        request = scenario_request(scenario, paths)
        configs.append(
            {
                "name": scenario.name,
                "funding_store": request.funding_path.name,
                "intents": [[i.action.value, i.decision_time, i.quantity] for i in request.intents],
                "spot_cost": describe_cost_model(request.spot_cost_model),
                "perpetual_cost": describe_cost_model(request.perpetual_cost_model),
            }
        )
        try:
            run = run_store_backed_multileg_replay(request)
        except Exception as exc:  # noqa: BLE001 - recorded; the run is failed, exit 1
            errors.append(f"{scenario.name}: {type(exc).__name__}: {exc}")
            checks.append(
                check(f"{scenario.name}.execution", "failed", str(exc), category="execution")
            )
            continue
        view = run_view(run)
        results[scenario.name] = view
        observed = [p["portfolio_equity"] for p in view["equity_timeline_pre_fill"]]
        ok = (
            observed == [Decimal(v) for v in scenario.expected_equities]
            and view["final_equity"] == Decimal(scenario.expected_final_equity)
            and view["position_open_at_end"] == scenario.expected_open_at_end
        )
        checks.append(
            check(
                f"{scenario.name}.hand_derived_expectation",
                "passed" if ok else "failed",
                f"equities {canonical_json(observed)}, final {view['final_equity']}",
                category="formula",
            )
        )
        checks.append(
            check(
                f"{scenario.name}.provenance_and_coverage",
                "passed",
                "spot/perpetual provenance equal the expected synthetic sources; "
                "coverage and every grid slot present; funding coverage PASS",
                category="data_integrity",
            )
        )
    config = {
        "fixture_version": FIXTURE_VERSION,
        "pair": [PAIR.pair_id, PAIR.spot.symbol, PAIR.perpetual.symbol, PAIR.spot.quote_asset],
        "timeframe": "1h",
        "window": [T0, T4],
        "as_of": T4,
        "expected_sources": {"spot": SPOT_SOURCE, "perpetual": PERP_SOURCE},
        "wallets": ["200", "200"],
        "funding_model": describe_funding_model(LinearFundingModel()),
        "decimal_context": normalize_decimal_context(None),
        "scenarios": configs,
    }
    return {
        "config": config,
        "config_sha256": fingerprint(config),
        "run_input_sha256": fingerprint(
            {"config": config, "inputs": {n: r["replay_input_sha256"] for n, r in results.items()}}
        ),
        "inputs": [{"role": role, "file_name": path.name} for role, path in sorted(paths.items())],
        "checks": checks,
        "results": results,
        "errors": errors,
        "limitations": [
            (
                "SYNTHETIC stores written by this run; NO real market data; SCRIPTED intents; "
                "NOT A STRATEGY"
            ),
            "per-store snapshots only: there is no atomic snapshot across the stores",
            "the funding store records no source; funding evidence is partition + coverage",
            (
                "valuation at trade CLOSE; no exchange mark-price series; no liquidation, legging, "
                "borrow, lot/tick rules or wallet transfers are modelled"
            ),
        ],
        "does_not_prove": [
            "profitability or real-exchange feasibility of any basis/carry trade",
            "provenance of any real dataset: the sources here are synthetic labels",
        ],
    }


def run_store_demo(output: Path, scenarios=offline.SCENARIOS, *, clock=lambda: datetime.now(UTC)):
    bundle = OutputBundle(output)
    try:
        deterministic = build_deterministic(bundle.staging / "fixture", scenarios)
    except Exception:
        bundle.discard()
        raise
    report = build_report(
        run_kind="multileg-store-demo", deterministic=deterministic, created_at=clock()
    )
    bundle.write_report(report)
    return bundle.commit(), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m crypto_quant_lab.research.multileg_store_demo",
        description="Store-backed multi-leg replay on new synthetic stores (offline).",
    )
    parser.add_argument("--output", type=Path, required=True, help="NEW directory for the bundle")
    args = parser.parse_args(argv)
    try:
        path, report = run_store_demo(args.output)
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{report['status']}: {path}")
    for item in report["deterministic"]["checks"]:
        print(f"  [{item['status']}] ({item['category']}) {item['name']}: {item['detail']}")
    for error in report["deterministic"]["errors"]:
        print(f"  error: {error}")
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
