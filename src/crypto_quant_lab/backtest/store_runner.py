"""Thin real-store orchestration entrypoint (BACKTEST_SPEC.md Bölüm 5, 6, 7, 35 madde 10;
FUNDING_SPEC.md Bölüm 16, FUNDING-SPEC MS11 explicit funding-mode contract).

`run_backtest_from_store` wires `prepare_backtest_dataset` (store -> quality
gate -> `tuple[Candle, ...]`) into `run_backtest_replay` (candle-by-candle
deterministic replay -> `BacktestResult`). No new quality/range/execution/
accounting/result logic — pure composition. `as_of_time` flows unmodified
into both calls, keeping the quality gate's finalized-data boundary and the
replay's global availability boundary aligned to the same point-in-time run.
Any exception raised by either composed function (quality gate failure,
storage error, replay/policy/cost_model error) propagates unchanged — this
module is not an error-translation layer.

`funding_required`/`funding_store`/`funding_model` are additive, keyword-only,
defaulted parameters (FUNDING-SPEC MS11). `funding_required=False` (the
default) preserves the exact pre-funding behavior — no funding store is ever
touched, and `funding_store`/`funding_model` must both be absent. This is an
explicit, high-level caller declaration — it is never inferred from
`market_type` (FUNDING_SPEC.md Bölüm 16: the canonical `market_type` string
has no locked spot/perpetual vocabulary and stays an opaque storage
partition key here, exactly as everywhere else) and never inferred merely
from whether `funding_store`/`funding_model` happen to be supplied.

When `funding_required=True`, both `funding_store` and `funding_model` are
mandatory. The funding canonical range `[candles[0].open_time,
feature_availability_time(candles[-1]))` is derived from the *already
quality-gated* candle dataset — guaranteeing byte-identical agreement with
whatever `run_backtest_replay` independently re-derives from the same
tuple. A `FundingDataQualityReport` must be `"PASS"` before any funding
event is queried or any economic replay work happens — a fully covered
zero-event range is a legal PASS (FUNDING_DATA_SPEC.md Bölüm 27), never
reinterpreted as missing history. The canonical events are then queried a
second, independent time (mirroring the exact quality-then-dataset
double-read `prepare_backtest_dataset` already performs for candles) and
re-validated for exact `exchange`/`market_type`/`symbol` partition match —
closing the one gap FUNDING-SPEC MS10's low-level replay could not close on
its own (a plain `Candle` carries no `exchange`/`market_type`).

`evaluation_start` is an additive, keyword-only, defaulted parameter
(VALIDATION_SPEC.md Bölüm 8.3, locked B2 mechanism), composing the
already-implemented `run_backtest_replay(..., evaluation_start=...)`
boundary: `requested_start` continues to mean the loaded historical/context
start, `requested_end` the loaded/evaluation range end (subject to the
existing `effective_end` clamp), and `evaluation_start` is the new economic
start passed straight through to canonical replay — this module never
implements context-candle skipping itself, that mechanism belongs entirely
to `replay.py`. When supplied, the economic funding query/quality range's
lower bound becomes `evaluation_start` instead of `candles[0].open_time`;
its upper bound is unchanged. `evaluation_start` is validated in two
stages: once from raw arguments before any store I/O (genuine/aware/
grid-aligned, within `[requested_start, requested_end)`), and once against
the actual prepared candle tuple after candle I/O but strictly before any
funding I/O — `prepare_backtest_dataset` may clamp `requested_end` down to
an earlier `effective_end`, so the raw check alone cannot guarantee at
least one evaluation candle remains.
"""

from datetime import datetime

from crypto_quant_lab.backtest.costs import CostModel
from crypto_quant_lab.backtest.dataset import prepare_backtest_dataset
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult
from crypto_quant_lab.backtest.policy import BacktestPolicy
from crypto_quant_lab.backtest.replay import run_backtest_replay
from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.funding.calculator import FundingModel
from crypto_quant_lab.funding.models import HistoricalFundingEvent
from crypto_quant_lab.funding.quality import build_funding_data_quality_report_from_store
from crypto_quant_lab.funding.store import HistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandleStore
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us


def _validate_funding_configuration(
    *,
    funding_required: object,
    funding_store: HistoricalFundingStore | None,
    funding_model: FundingModel | None,
) -> None:
    """Reject any contradictory funding configuration before any store I/O.

    `funding_required` is never inferred — it must be a genuine `bool`, and
    its value alone determines whether `funding_store`/`funding_model` are
    required (both) or forbidden (both). No partial/contradictory
    combination is legal.
    """
    if not isinstance(funding_required, bool):
        raise TypeError(f"funding_required must be a bool, got {type(funding_required).__name__}")

    if funding_required:
        if funding_store is None:
            raise ValueError("funding_store must be supplied when funding_required=True")
        if funding_model is None:
            raise ValueError("funding_model must be supplied when funding_required=True")
    else:
        if funding_store is not None:
            raise ValueError("funding_store must not be supplied when funding_required=False")
        if funding_model is not None:
            raise ValueError("funding_model must not be supplied when funding_required=False")


def _validate_evaluation_start_configuration(
    evaluation_start: datetime | None,
    *,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
) -> None:
    """Reject an invalid `evaluation_start` before any store I/O (Stage A).

    `None` means no context — no new checks, exact legacy behavior. An
    explicit boundary must be a genuine, aware, grid-aligned datetime within
    `[requested_start, requested_end)`. This is necessary but not
    sufficient: `prepare_backtest_dataset` may later clamp `requested_end`
    down to an earlier `effective_end`, which only the prepared candle
    tuple reveals — see `_validate_evaluation_start_against_candles`.
    """
    if evaluation_start is None:
        return

    evaluation_start_us = datetime_to_epoch_us(evaluation_start)

    if not is_grid_aligned(evaluation_start, timeframe):
        raise ValueError(
            f"evaluation_start is not aligned to the {timeframe!r} grid: {evaluation_start!r}"
        )

    requested_start_us = datetime_to_epoch_us(requested_start)
    requested_end_us = datetime_to_epoch_us(requested_end)

    if not (requested_start_us <= evaluation_start_us < requested_end_us):
        raise ValueError(
            "evaluation_start must satisfy requested_start <= evaluation_start < "
            f"requested_end, got evaluation_start={evaluation_start!r}, "
            f"requested_start={requested_start!r}, requested_end={requested_end!r}"
        )


def _validate_evaluation_start_against_candles(
    evaluation_start: datetime | None, *, candles: tuple[Candle, ...]
) -> None:
    """Re-check `evaluation_start` against the actual prepared candles (Stage B).

    Runs after candle I/O but strictly before any funding I/O. Only the
    upper bound is re-checked — `prepare_backtest_dataset` never shifts the
    start (`candles[0].open_time == requested_start`) and timeframe is
    invariant, so Stage A's lower-bound/type/grid checks already stand; only
    the true, possibly-clamped `effective_end` was unknowable before candle
    I/O.
    """
    if evaluation_start is None:
        return

    evaluation_start_us = datetime_to_epoch_us(evaluation_start)
    run_end = feature_availability_time(candles[-1])
    run_end_us = datetime_to_epoch_us(run_end)

    if evaluation_start_us >= run_end_us:
        raise ValueError(
            "evaluation_start must leave at least one evaluation candle in the prepared "
            f"dataset: evaluation_start={evaluation_start!r} is not before "
            f"feature_availability_time(candles[-1])={run_end!r}"
        )


def _query_canonical_funding_events(
    funding_store: HistoricalFundingStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    funding_run_start: datetime,
    funding_run_end: datetime,
) -> tuple[HistoricalFundingEvent, ...]:
    """Quality-gate, then query, the canonical funding history for one exact partition/range.

    Two independent reads, mirroring `prepare_backtest_dataset`'s own
    established quality-then-dataset double-read for candles: the quality
    report (which performs its own internal `query_events`/`query_coverage`
    calls) decides PASS/FAIL first, and only on PASS is `query_events`
    called again here to obtain the actual sequence. Every returned event
    is independently re-validated for exact partition match — this store
    has the real `exchange`/`market_type` ground truth a plain `Candle`
    never carries, closing FUNDING-SPEC MS10's replay-layer limitation.
    """
    report = build_funding_data_quality_report_from_store(
        funding_store,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        requested_start=funding_run_start,
        requested_end=funding_run_end,
    )
    if report.overall_status != "PASS":
        raise ValueError(
            "funding quality gate failed: overall_status="
            f"{report.overall_status!r} for exchange={exchange!r}, market_type={market_type!r}, "
            f"symbol={symbol!r}, funding_run_start={funding_run_start!r}, "
            f"funding_run_end={funding_run_end!r}, "
            f"coverage_gap_count={report.coverage_gap_count}, "
            f"coverage_gaps={report.coverage_gaps!r}"
        )

    events = funding_store.query_events(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        start_time=funding_run_start,
        end_time=funding_run_end,
    )

    for event in events:
        if not isinstance(event, HistoricalFundingEvent):
            raise TypeError(
                "funding_store.query_events must return HistoricalFundingEvent instances, got "
                f"{type(event).__name__}"
            )
        if event.exchange != exchange or event.market_type != market_type or event.symbol != symbol:
            raise ValueError(
                "queried funding event partition must match the requested "
                f"exchange/market_type/symbol: expected ({exchange!r}, {market_type!r}, "
                f"{symbol!r}), got ({event.exchange!r}, {event.market_type!r}, {event.symbol!r})"
            )

    return tuple(events)


def run_backtest_from_store(
    store: HistoricalCandleStore,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    requested_start: datetime,
    requested_end: datetime,
    as_of_time: datetime,
    config: BacktestConfig,
    policy: BacktestPolicy,
    cost_model: CostModel,
    funding_required: bool = False,
    funding_store: HistoricalFundingStore | None = None,
    funding_model: FundingModel | None = None,
    evaluation_start: datetime | None = None,
) -> BacktestResult:
    """Run a deterministic backtest over `store`'s quality-gated data for `[requested_start, requested_end)`.

    `funding_required` is validated first, before any store I/O at all —
    see `_validate_funding_configuration`. `evaluation_start` (`None` by
    default — exact legacy behavior) is then validated from raw arguments,
    also before any store I/O — see `_validate_evaluation_start_configuration`.
    When `funding_required=True`, the funding-aware path is: candle dataset
    prepared as always, then `evaluation_start` re-validated against the
    actual prepared candles (`_validate_evaluation_start_against_candles`,
    strictly before any funding I/O), then the canonical funding range/
    quality-gate/event-query composition in `_query_canonical_funding_events`
    — whose lower bound is `evaluation_start` if supplied, else
    `candles[0].open_time` — then a single funding-aware `run_backtest_replay`
    call with `evaluation_start` forwarded unchanged — no postprocessing, no
    result mutation.
    """
    _validate_funding_configuration(
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )
    _validate_evaluation_start_configuration(
        evaluation_start,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
    )

    candles = prepare_backtest_dataset(
        store,
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        as_of_time=as_of_time,
    )
    _validate_evaluation_start_against_candles(evaluation_start, candles=candles)

    funding_events: tuple[HistoricalFundingEvent, ...] = ()
    if funding_required:
        funding_run_start = (
            evaluation_start if evaluation_start is not None else candles[0].open_time
        )
        funding_run_end = feature_availability_time(candles[-1])
        funding_events = _query_canonical_funding_events(
            funding_store,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            funding_run_start=funding_run_start,
            funding_run_end=funding_run_end,
        )

    return run_backtest_replay(
        candles,
        as_of_time=as_of_time,
        config=config,
        policy=policy,
        cost_model=cost_model,
        funding_events=funding_events,
        funding_model=funding_model,
        evaluation_start=evaluation_start,
    )
