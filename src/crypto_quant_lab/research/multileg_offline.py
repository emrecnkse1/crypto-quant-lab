"""Synthetic offline demo of the in-memory multi-leg replay (FUNDING_RESEARCH_SPEC.md Bölüm 19.12.8).

    python -m crypto_quant_lab.research.multileg_offline --output <NEW directory>

SYNTHETIC DATA · SCRIPTED INTENTS · NOT A STRATEGY. Runs fixed, versioned
synthetic scenarios through the production `run_multileg_replay`, then checks
the results against hand-derived expectations written below (not produced
by the code under test). No network, no store, no existing database. Output
is a report v2 bundle (research/report.py): deterministic JSON + Markdown in a
NEW directory, never overwriting; a failure produces a failed bundle.
Computations run in a fresh Decimal context from decimal_policy's documented
default; the wall clock and git revision only appear in `run_metadata`.

Synthetic market (1h, T0 = 2026-01-01T00:00Z; spot OPEN/CLOSE, perp OPEN/CLOSE):
  T0 100/100 102/102 · T1 100/101 102/102 · T2 101/101 102/101 · T3 101/101 101/101
Scripted intents: OPEN decided at T1 (fills T1 OPENs 100 / 102), CLOSE decided
at T3 (fills T3 OPENs 101 / 101). Wallets 200 spot cash + 200 collateral, qty 1.
Hand-derived expectations (pre-fill marks at T1..T4, final equity):
  closed_no_funding       400, 401, 402, 402                  -> 402
  close_instant_funding   funding T3 rate 0.0001 ref 101: -1*101*0.0001 = -0.0101
                          400, 401, 402.0101, 402.0101        -> 402.0101
  proportional_costs_and_funding  spot fee 0.001, perp fee 0.0005 of notional:
                          open 0.100 / 0.0510, close 0.101 / 0.0505
                          400, 400.8490, 401.8591, 401.7076   -> 401.7076
                          (spot 200.799, collateral 200.9086)
  open_at_end             OPEN only: 400, 401, 402, 402; final 402 with spot
                          and perp unrealized 1 each, lifecycle HEDGED_OPEN
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from crypto_quant_lab.backtest.costs import CostModel, ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.multileg import HedgedPair, TradableInstrument
from crypto_quant_lab.backtest.multileg_replay import (
    HedgeAction,
    HedgeIntent,
    LegCandles,
    MultiLegReplayResult,
    run_multileg_replay,
)
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research.decimal_policy import build_context, normalize_decimal_context
from crypto_quant_lab.research.report import (
    OutputBundle,
    build_report,
    canonical_json,
    check,
    fingerprint,
)
from crypto_quant_lab.storage.datasets import BINANCE_USDM_KLINES_SOURCE, CandleDataset

SCENARIO_VERSION = "multileg-offline/v1"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_H = timedelta(hours=1)
_SPOT_ROWS = (("100", "100"), ("100", "101"), ("101", "101"), ("101", "101"))
_PERP_ROWS = (("102", "102"), ("102", "102"), ("102", "101"), ("101", "101"))
_SPOT_DS = CandleDataset("binance", "spot", "SYNTHUSDT", "1h", "spot_trade", "synthetic:spot")
_PERP_DS = CandleDataset(
    "binance", "usdm_perpetual", "SYNTHUSDT", "1h", "contract_trade", BINANCE_USDM_KLINES_SOURCE
)
_PAIR = HedgedPair(
    "synthetic-spot-perp",
    TradableInstrument("binance", "spot", "SYNTHUSDT", "USDT"),
    TradableInstrument("binance", "usdm_perpetual", "SYNTHUSDT", "USDT"),
)


@dataclass(frozen=True)
class Scenario:
    name: str
    close: bool
    fundings: tuple[tuple[datetime, str], ...]
    spot_fee_rate: str
    perp_fee_rate: str
    expected_equities: tuple[str, ...]
    expected_final_equity: str
    expected_open_at_end: bool


SCENARIOS = (
    Scenario("closed_no_funding", True, (), "0", "0", ("400", "401", "402", "402"), "402", False),
    Scenario(
        "close_instant_funding",
        True,
        ((_T0 + 3 * _H, "0.0001"),),
        "0",
        "0",
        ("400", "401", "402.0101", "402.0101"),
        "402.0101",
        False,
    ),
    Scenario(
        "proportional_costs_and_funding",
        True,
        ((_T0 + 3 * _H, "0.0001"),),
        "0.001",
        "0.0005",
        ("400", "400.8490", "401.8591", "401.7076"),
        "401.7076",
        False,
    ),
    Scenario("open_at_end", False, (), "0", "0", ("400", "401", "402", "402"), "402", True),
)


def _series(rows, dataset):
    candles = []
    for k, (open_, close) in enumerate(rows):
        o, c = Decimal(open_), Decimal(close)
        candles.append(
            Candle(dataset.symbol, "1h", _T0 + k * _H, o, max(o, c), min(o, c), c, Decimal(1))
        )
    return LegCandles(dataset, tuple(candles))


def _cost(rate: str) -> CostModel:
    return (
        ZeroCostModel() if Decimal(rate) == 0 else ProportionalCommissionModel(rate=Decimal(rate))
    )


def run_scenario(scenario: Scenario) -> MultiLegReplayResult:
    intents = [HedgeIntent(HedgeAction.OPEN, _T0 + _H, Decimal(1))]
    if scenario.close:
        intents.append(HedgeIntent(HedgeAction.CLOSE, _T0 + 3 * _H, Decimal(1)))
    fundings = tuple(
        HistoricalFundingEvent(
            exchange="binance",
            market_type="usdm_perpetual",
            symbol="SYNTHUSDT",
            funding=FundingEvent(
                event_time=time,
                funding_rate=Decimal(rate),
                reference_price=Decimal(101),
                rate_type="Regular",
            ),
        )
        for time, rate in scenario.fundings
    )
    return run_multileg_replay(
        pair=_PAIR,
        spot=_series(_SPOT_ROWS, _SPOT_DS),
        perpetual=_series(_PERP_ROWS, _PERP_DS),
        funding_events=fundings,
        intents=tuple(intents),
        spot_cash=Decimal(200),
        perpetual_collateral=Decimal(200),
        spot_cost_model=_cost(scenario.spot_fee_rate),
        perpetual_cost_model=_cost(scenario.perp_fee_rate),
        funding_model=LinearFundingModel(),
        as_of_time=_T0 + 4 * _H,
    )


def result_view(result: MultiLegReplayResult) -> dict:
    final, mark = result.final_state, result.final_mark
    return {
        "lifecycle_at_end": final.lifecycle.value,
        "position_open_at_end": result.position_open_at_end,
        "wallets": {
            "spot_cash": final.spot.cash,
            "spot_quantity": final.spot.quantity,
            "perpetual_collateral": final.perpetual.collateral,
            "perpetual_quantity": final.perpetual.quantity,
        },
        "realized_pnl": {
            "spot": final.spot.realized_pnl,
            "perpetual": final.perpetual.realized_pnl,
        },
        "unrealized_pnl_at_end": {
            "spot": mark.spot_asset_value
            - (final.spot.quantity * final.spot.entry_price if final.spot.entry_price else 0),
            "perpetual": mark.perpetual_unrealized_pnl,
        },
        "costs": {"spot": final.spot.costs_paid, "perpetual": final.perpetual.costs_paid},
        "funding_paid": final.perpetual.funding_paid,
        "final_equity": mark.portfolio_equity,
        "total_pnl": mark.total_pnl,
        "fills": [
            {
                "action": f.action,
                "time": f.time,
                "spot": {
                    "side": f.spot.execution.side.value,
                    "price": f.spot.execution.price,
                    "quantity": f.spot.execution.quantity,
                    "cost": f.spot.cost,
                },
                "perpetual": {
                    "side": f.perpetual.execution.side.value,
                    "price": f.perpetual.execution.price,
                    "quantity": f.perpetual.execution.quantity,
                    "cost": f.perpetual.cost,
                },
            }
            for f in result.paired_fills
        ],
        "funding_records": [
            {
                "event_time": r.event.funding.event_time,
                "rate_type": r.event.funding.rate_type,
                "rate": r.event.funding.funding_rate,
                "reference_price": r.event.funding.reference_price,
                "lifecycle_before": r.lifecycle_before.value,
                "pre_fill_perpetual_quantity": r.pre_fill_perpetual_quantity,
                "signed_cost": r.signed_cost,
            }
            for r in result.funding_records
        ],
        "equity_timeline_pre_fill": [
            {
                "time": p.mark.time,
                "candle_open_time": p.candle_open_time,
                "spot_equity": p.mark.spot_equity,
                "perpetual_margin_equity": p.mark.perpetual_margin_equity,
                "portfolio_equity": p.mark.portfolio_equity,
            }
            for p in result.equity_points
        ],
        "unexecuted_intents": [
            {
                "action": u.intent.action.value,
                "decision_time": u.intent.decision_time,
                "reason": u.reason,
            }
            for u in result.unexecuted_intents
        ],
        "trace": [[e.time, e.kind.value, e.detail] for e in result.trace],
        "scope": {
            "intents_are_exogenous": result.intents_are_exogenous,
            "valuation_source": result.valuation_source,
            "liquidation_not_modeled": result.liquidation_not_modeled,
            "legging_not_modeled": result.legging_not_modeled,
            "warmup_supported": result.warmup_supported,
        },
    }


def _input_manifest() -> list[dict]:
    return [
        {
            "role": "spot",
            "dataset": [*_SPOT_DS.namespace, _SPOT_DS.price_kind, _SPOT_DS.source],
            "logical_fingerprint_sha256": fingerprint(_SPOT_ROWS),
        },
        {
            "role": "perpetual",
            "dataset": [*_PERP_DS.namespace, _PERP_DS.price_kind, _PERP_DS.source],
            "logical_fingerprint_sha256": fingerprint(_PERP_ROWS),
        },
    ]


def build_deterministic(scenarios=SCENARIOS) -> dict:
    decimal_context = normalize_decimal_context(None)
    config = {
        "scenario_version": SCENARIO_VERSION,
        "decimal_context": decimal_context,
        "scenarios": [
            {
                "name": s.name,
                "close": s.close,
                "fundings": [[t, r] for t, r in s.fundings],
                "spot_fee_rate": s.spot_fee_rate,
                "perp_fee_rate": s.perp_fee_rate,
            }
            for s in scenarios
        ],
    }
    results, checks, errors = {}, [], []
    with localcontext(build_context(decimal_context)):
        for scenario in scenarios:
            try:
                view = result_view(run_scenario(scenario))
            except Exception as exc:  # noqa: BLE001 - recorded; the run is failed, exit 1
                errors.append(f"{scenario.name}: {type(exc).__name__}: {exc}")
                checks.append(
                    check(f"{scenario.name}.execution", "failed", str(exc), category="execution")
                )
                continue
            results[scenario.name] = view
            observed = [p["portfolio_equity"] for p in view["equity_timeline_pre_fill"]]
            expected = [Decimal(v) for v in scenario.expected_equities]
            ok = (
                observed == expected
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
    config_view = config
    inputs = _input_manifest()
    return {
        "config": config_view,
        "config_sha256": fingerprint(config_view),
        "run_input_sha256": fingerprint({"config": config_view, "inputs": inputs}),
        "inputs": inputs,
        "checks": checks,
        "results": results,
        "errors": errors,
        "limitations": [
            "SYNTHETIC data and SCRIPTED intents; NOT A STRATEGY and not market evidence",
            "valuation at trade CLOSE prices; no exchange mark-price series is used",
            (
                "no liquidation, margin tiers, legging/partial fills, borrow, lot/tick rules or "
                "wallet transfers are modelled; negative margin equity would only be displayed"
            ),
            "equity points are PRE-fill marks at each candle availability instant",
        ],
        "does_not_prove": [
            "profitability, tradability or real-exchange feasibility of any basis/carry trade",
            "that scripted intents are free of hindsight (they are exogenous inputs)",
        ],
    }


def run_offline_demo(output: Path, scenarios=SCENARIOS, *, clock=lambda: datetime.now(UTC)):
    bundle = OutputBundle(output)
    report = build_report(
        run_kind="multileg-offline-demo",
        deterministic=build_deterministic(scenarios),
        created_at=clock(),
    )
    bundle.write_report(report)
    return bundle.commit(), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m crypto_quant_lab.research.multileg_offline",
        description="Synthetic multi-leg replay demo (offline; SYNTHETIC, NOT A STRATEGY).",
    )
    parser.add_argument("--output", type=Path, required=True, help="NEW directory for the bundle")
    args = parser.parse_args(argv)
    try:
        path, report = run_offline_demo(args.output)
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
