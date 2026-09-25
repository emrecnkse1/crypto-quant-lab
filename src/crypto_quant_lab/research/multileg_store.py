"""Read-only store-backed multi-leg replay (FUNDING_RESEARCH_SPEC.md Bölüm 19.13).

Loads spot trade candles, perpetual contract-trade candles and settled funding
from EXISTING stores through the genuinely read-only path
(`open_read_only` + `read_snapshot`), validates provenance and coverage, and
hands the loaded data unchanged to `run_multileg_replay`. It never writes to a
source store, never downloads, never repairs, and never calls the replay when
any check fails.

Evidence it can honestly give (and nothing more):
- candles: the namespace's registered provenance (`CandleDataset`: exchange,
  market_type, symbol, timeframe, price_kind, source) must equal the
  expected identity, including the caller-declared source label; the store's
  authoritative coverage must contain [run_start, run_end); every grid slot
  must hold a candle (coverage without the candle = authoritative upstream
  absence, which the replay cannot fill);
- funding: the funding store has NO source column, so only the partition
  (exchange, market_type, symbol) and its coverage are evidence; the
  existing quality report must PASS over [run_start, run_end). Verified
  coverage with no events is a real zero-funding window; missing coverage is
  refused, never treated as zero funding;
- consistency: every store is read inside ONE read transaction of its own
  (rows, provenance and coverage of that store come from one committed
  state). Different stores are separate snapshots taken one after another:
  there is NO atomic snapshot across stores.
- fingerprints: `replay_input_sha256` covers the consumed candle values,
  funding events, window, intents, wallets, cost/funding model parameters
  and Decimal context; it is None when a model has no describable
  parameters (only its class would be known). `store_provenance_sha256` of
  each store covers its dataset identity and coverage, separately.
"""

from contextlib import ExitStack
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
from crypto_quant_lab.backtest.multileg import HedgedPair
from crypto_quant_lab.backtest.multileg_replay import (
    HedgeIntent,
    LegCandles,
    MultiLegReplayResult,
    run_multileg_replay,
)
from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.funding.calculator import FundingModel, LinearFundingModel
from crypto_quant_lab.funding.quality import build_funding_data_quality_report_from_store
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.research.decimal_policy import build_context, normalize_decimal_context
from crypto_quant_lab.research.report import fingerprint
from crypto_quant_lab.storage.datasets import (
    CONTRACT_TRADE,
    SPOT_TRADE,
    CandleCoverageInterval,
    CandleDataset,
    coverage_contains,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us

SNAPSHOT_SCOPE = (
    "per-store read transaction: each store's rows, provenance and coverage come from one "
    "committed state; stores are read one after another and there is no atomic snapshot "
    "across stores"
)
REASONS = (
    "invalid_request",
    "missing_provenance",
    "provenance_mismatch",
    "incomplete_coverage",
    "missing_data",
)


class StoreInputError(ValueError):
    """A store input cannot be used; `reason` is one of REASONS."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown reason {reason!r}")
        super().__init__(f"[{reason}] {message}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CandleStoreSource:
    """A candle store file plus the dataset source label the caller accepts."""

    path: Path
    expected_source: str


@dataclass(frozen=True, slots=True)
class StoreBackedMultiLegRequest:
    pair: HedgedPair
    timeframe: str
    run_start: datetime
    run_end: datetime
    as_of_time: datetime
    spot: CandleStoreSource
    perpetual: CandleStoreSource
    funding_path: Path
    intents: tuple[HedgeIntent, ...]
    spot_cash: Decimal
    perpetual_collateral: Decimal
    spot_cost_model: CostModel
    perpetual_cost_model: CostModel
    funding_model: FundingModel
    decimal_context: dict


@dataclass(frozen=True, slots=True)
class CandleEvidence:
    role: str
    file_name: str
    dataset: CandleDataset
    coverage: tuple[CandleCoverageInterval, ...]
    candle_count: int
    consumed_sha256: str
    store_provenance_sha256: str


@dataclass(frozen=True, slots=True)
class FundingEvidence:
    file_name: str
    partition: tuple[str, str, str]
    quality_status: str
    coverage_gap_count: int
    event_count: int
    consumed_sha256: str
    source_recorded: bool = False  # the funding store schema has no source column


@dataclass(frozen=True, slots=True)
class StoreBackedMultiLegRun:
    request: StoreBackedMultiLegRequest
    spot: CandleEvidence
    perpetual: CandleEvidence
    funding: FundingEvidence
    replay_input_sha256: str | None
    result: MultiLegReplayResult
    snapshot_scope: str = SNAPSHOT_SCOPE


def describe_cost_model(model: object) -> dict | None:
    """Exact parameters of the existing cost models; None when not describable."""
    if isinstance(model, ZeroCostModel):
        return {"model": "zero"}
    if isinstance(model, ProportionalCommissionModel):
        return {"model": "proportional_commission", "rate": model.rate}
    if isinstance(model, ProportionalSpreadCostModel):
        return {"model": "proportional_spread", "half_spread_rate": model.half_spread_rate}
    if isinstance(model, ProportionalSlippageCostModel):
        return {"model": "proportional_slippage", "rate": model.rate}
    if isinstance(model, CompositeCostModel):
        parts = [describe_cost_model(c) for c in model.components]
        return (
            None if any(p is None for p in parts) else {"model": "composite", "components": parts}
        )
    return None


def describe_funding_model(model: object) -> dict | None:
    return {"model": "linear"} if isinstance(model, LinearFundingModel) else None


def _validate_request(request: StoreBackedMultiLegRequest) -> dict:
    if not isinstance(request, StoreBackedMultiLegRequest):
        raise TypeError("request must be a StoreBackedMultiLegRequest")
    try:
        candle_duration(request.timeframe)
        start_us = datetime_to_epoch_us(request.run_start)
        end_us = datetime_to_epoch_us(request.run_end)
        as_of_us = datetime_to_epoch_us(request.as_of_time)
        decimal_context = normalize_decimal_context(request.decimal_context)
    except (TypeError, ValueError) as exc:
        raise StoreInputError("invalid_request", str(exc)) from exc
    if start_us >= end_us:
        raise StoreInputError("invalid_request", "run_start must be before run_end")
    if not (
        is_grid_aligned(request.run_start, request.timeframe)
        and is_grid_aligned(request.run_end, request.timeframe)
    ):
        raise StoreInputError("invalid_request", "run window must be aligned to the timeframe grid")
    if end_us > as_of_us:
        raise StoreInputError("invalid_request", "run_end must be <= as_of_time")
    for name in ("spot", "perpetual"):
        source = getattr(request, name)
        if not isinstance(source, CandleStoreSource) or not source.expected_source:
            raise StoreInputError("invalid_request", f"{name} needs a CandleStoreSource")
    return decimal_context


def _expected_dataset(request, instrument, price_kind, expected_source) -> CandleDataset:
    return CandleDataset(
        exchange=instrument.exchange,
        market_type=instrument.market_type,
        symbol=instrument.symbol,
        timeframe=request.timeframe,
        price_kind=price_kind,
        source=expected_source,
    )


def _load_leg(
    stack: ExitStack,
    role: str,
    source: CandleStoreSource,
    expected: CandleDataset,
    request: StoreBackedMultiLegRequest,
) -> tuple[LegCandles, CandleEvidence]:
    store = SQLiteHistoricalCandleStore.open_read_only(source.path)
    stack.callback(store.close)
    with store.read_snapshot():
        registered = store.query_dataset(*expected.namespace)
        coverage = tuple(
            store.query_coverage(*expected.namespace, request.run_start, request.run_end)
        )
        rows = store.query(*expected.namespace, request.run_start, request.run_end)
    if registered is None:
        raise StoreInputError(
            "missing_provenance",
            f"{role} namespace {list(expected.namespace)} has no registered provenance "
            "(legacy or foreign rows are never assumed to be this dataset)",
        )
    if registered != expected:
        raise StoreInputError(
            "provenance_mismatch",
            f"{role} namespace is registered as price_kind={registered.price_kind!r}, "
            f"source={registered.source!r}; expected price_kind={expected.price_kind!r}, "
            f"source={expected.source!r}",
        )
    if not coverage_contains(coverage, request.run_start, request.run_end):
        raise StoreInputError(
            "incomplete_coverage",
            f"{role} authoritative coverage does not contain [run_start, run_end)",
        )
    slots = (request.run_end - request.run_start) // candle_duration(request.timeframe)
    if len(rows) != slots:
        raise StoreInputError(
            "missing_data",
            f"{role} has {len(rows)} of {slots} candles inside its authoritative coverage; "
            "absent candles are never filled",
        )
    candles = tuple(r.candle for r in rows)
    identity = [
        registered.exchange,
        registered.market_type,
        registered.symbol,
        registered.timeframe,
        registered.price_kind,
        registered.source,
    ]
    coverage_view = [[c.start_time, c.end_time] for c in coverage]
    evidence = CandleEvidence(
        role=role,
        file_name=Path(source.path).name,
        dataset=registered,
        coverage=coverage,
        candle_count=len(candles),
        consumed_sha256=fingerprint(_candle_values(candles)),
        store_provenance_sha256=fingerprint({"dataset": identity, "coverage": coverage_view}),
    )
    return LegCandles(registered, candles), evidence


def _candle_values(candles) -> list:
    return [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in candles]


def _funding_values(events) -> list:
    return [
        [
            e.funding.event_time,
            e.funding.rate_type,
            e.funding.funding_rate,
            e.funding.reference_price,
        ]
        for e in events
    ]


def _load_funding(stack: ExitStack, request: StoreBackedMultiLegRequest):
    perp = request.pair.perpetual
    partition = (perp.exchange, perp.market_type, perp.symbol)
    store = SQLiteHistoricalFundingStore.open_read_only(request.funding_path)
    stack.callback(store.close)
    with store.read_snapshot():
        report = build_funding_data_quality_report_from_store(
            store,
            exchange=partition[0],
            market_type=partition[1],
            symbol=partition[2],
            requested_start=request.run_start,
            requested_end=request.run_end,
        )
        events = tuple(
            store.query_events(
                exchange=partition[0],
                market_type=partition[1],
                symbol=partition[2],
                start_time=request.run_start,
                end_time=request.run_end,
            )
        )
    if report.overall_status != "PASS":
        raise StoreInputError(
            "incomplete_coverage",
            f"funding coverage over [run_start, run_end) is {report.overall_status} with "
            f"{report.coverage_gap_count} gap(s); uncollected funding is never treated as zero",
        )
    evidence = FundingEvidence(
        file_name=Path(request.funding_path).name,
        partition=partition,
        quality_status=report.overall_status,
        coverage_gap_count=report.coverage_gap_count,
        event_count=len(events),
        consumed_sha256=fingerprint(_funding_values(events)),
    )
    return events, evidence


def replay_input_fingerprint(request, decimal_context, spot, perpetual, events) -> str | None:
    costs = [
        describe_cost_model(request.spot_cost_model),
        describe_cost_model(request.perpetual_cost_model),
    ]
    funding_model = describe_funding_model(request.funding_model)
    if None in costs or funding_model is None:
        return None
    pair = request.pair
    return fingerprint(
        {
            "pair": [
                pair.pair_id,
                pair.spot.exchange,
                pair.spot.symbol,
                pair.spot.quote_asset,
                pair.perpetual.exchange,
                pair.perpetual.symbol,
                pair.hedge_ratio,
            ],
            "timeframe": request.timeframe,
            "window": [request.run_start, request.run_end],
            "as_of": request.as_of_time,
            "spot": _candle_values(spot.candles),
            "perpetual": _candle_values(perpetual.candles),
            "funding": _funding_values(events),
            "intents": [[i.action.value, i.decision_time, i.quantity] for i in request.intents],
            "wallets": [request.spot_cash, request.perpetual_collateral],
            "costs": costs,
            "funding_model": funding_model,
            "decimal_context": decimal_context,
        }
    )


@dataclass(frozen=True, slots=True)
class PreparedStoreInputs:
    """Validated request + the data each store yielded inside its own read snapshot (no replay)."""

    request: StoreBackedMultiLegRequest
    decimal_context: dict
    spot: LegCandles
    perpetual: LegCandles
    funding_events: tuple
    spot_evidence: CandleEvidence
    perpetual_evidence: CandleEvidence
    funding_evidence: FundingEvidence


def prepare_store_backed_inputs(request: StoreBackedMultiLegRequest) -> PreparedStoreInputs:
    """Validate the request and load every store read-only (one snapshot per store).

    Shared by the replay runner and the replay-free `multileg-doctor`: raises
    StoreInputError (or FileNotFoundError for an absent store) on bad input;
    all connections are closed before it returns. It never replays, fills,
    marks or settles anything.
    """
    decimal_context = _validate_request(request)
    pair = request.pair
    with ExitStack() as stack:
        spot, spot_evidence = _load_leg(
            stack,
            "spot",
            request.spot,
            _expected_dataset(request, pair.spot, SPOT_TRADE, request.spot.expected_source),
            request,
        )
        perpetual, perpetual_evidence = _load_leg(
            stack,
            "perpetual",
            request.perpetual,
            _expected_dataset(
                request, pair.perpetual, CONTRACT_TRADE, request.perpetual.expected_source
            ),
            request,
        )
        events, funding_evidence = _load_funding(stack, request)
    return PreparedStoreInputs(
        request=request,
        decimal_context=decimal_context,
        spot=spot,
        perpetual=perpetual,
        funding_events=events,
        spot_evidence=spot_evidence,
        perpetual_evidence=perpetual_evidence,
        funding_evidence=funding_evidence,
    )


def run_store_backed_multileg_replay(request: StoreBackedMultiLegRequest) -> StoreBackedMultiLegRun:
    """Validate, load read-only, replay; raises StoreInputError before any replay on bad input."""
    prepared = prepare_store_backed_inputs(request)
    decimal_context = prepared.decimal_context
    spot, perpetual, events = prepared.spot, prepared.perpetual, prepared.funding_events
    with localcontext(build_context(decimal_context)):
        result = run_multileg_replay(
            pair=request.pair,
            spot=spot,
            perpetual=perpetual,
            funding_events=events,
            intents=request.intents,
            spot_cash=request.spot_cash,
            perpetual_collateral=request.perpetual_collateral,
            spot_cost_model=request.spot_cost_model,
            perpetual_cost_model=request.perpetual_cost_model,
            funding_model=request.funding_model,
            as_of_time=request.as_of_time,
        )
        fingerprint_value = replay_input_fingerprint(
            request, decimal_context, spot, perpetual, events
        )
    return StoreBackedMultiLegRun(
        request=request,
        spot=prepared.spot_evidence,
        perpetual=prepared.perpetual_evidence,
        funding=prepared.funding_evidence,
        replay_input_sha256=fingerprint_value,
        result=result,
    )


def default_request(**fields) -> StoreBackedMultiLegRequest:
    """Convenience constructor: zero costs, linear funding, documented Decimal default."""
    defaults = {
        "spot_cost_model": ZeroCostModel(),
        "perpetual_cost_model": ZeroCostModel(),
        "funding_model": LinearFundingModel(),
        "decimal_context": normalize_decimal_context(None),
    }
    return StoreBackedMultiLegRequest(**(defaults | fields))
