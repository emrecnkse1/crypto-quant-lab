"""Faz 7 first slice: leakage-safe funding-carry research (FUNDING_RESEARCH_SPEC.md).

Expected cash/equity values are hand-computed from the locked contracts
(fill at next open, LinearFundingModel cost = signed_qty * reference_price *
rate, positive cost = cash outflow; proportional commission = notional *
rate) — never produced by calling the production helpers.
"""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import crypto_quant_lab.backtest.replay as replay_module
import crypto_quant_lab.backtest.store_runner as store_runner_module
import crypto_quant_lab.research.funding_carry as funding_carry_module
import crypto_quant_lab.validation.rolling as rolling_module
from crypto_quant_lab.backtest.costs import ProportionalCommissionModel, ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, PositionTarget
from crypto_quant_lab.backtest.policy import PolicyContext
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.research.funding_carry import (
    FUNDING_CARRY_STRATEGY,
    NO_TRADE_CONTROL_STRATEGY,
    FundingCarryPolicy,
    FundingSignalHistory,
    NoTradeControlPolicy,
    evaluate_funding_research_candidate,
    funding_carry_candidate,
    funding_research_policy_factory,
    load_funding_signal_history,
    no_trade_control_candidate,
)
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.annualized_metrics import compute_annualized_sharpe_ratio
from crypto_quant_lab.validation.metrics import compute_stage1_metrics, compute_stage2_metrics
from crypto_quant_lab.validation.rolling import (
    ContextAwareWindow,
    run_context_aware_rolling_backtest_from_store,
)
from crypto_quant_lab.validation.trial_group import TrialGroup, recorded_trial_count
from crypto_quant_lab.validation.windows import TemporalWindow

EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
AS_OF_TIME = T0 + HOUR * 48
COVERAGE_END = T0 + HOUR * 25
CONFIG = BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1))
SHORT_ENTRY = Decimal("0.0005")
LONG_ENTRY = Decimal("-0.0005")
MAX_AGE = timedelta(hours=9)
FIRST_WINDOW = TemporalWindow(start=T0, end=T0 + HOUR * 12)
SECOND_WINDOW = TemporalWindow(start=T0 + HOUR * 12, end=T0 + HOUR * 24)


def _event(hour, rate, *, rate_type="Regular", minute=0):
    return FundingEvent(
        event_time=T0 + HOUR * hour + timedelta(minutes=minute),
        funding_rate=Decimal(rate),
        reference_price=Decimal(100),
        rate_type=rate_type,
    )


def _history(events, *, lag=timedelta(0), start=T0, end=COVERAGE_END):
    return FundingSignalHistory(
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        coverage_start=start,
        coverage_end=end,
        publication_lag=lag,
        events=tuple(events),
    )


def _policy(history, *, max_age=MAX_AGE):
    return FundingCarryPolicy(
        history, short_entry_rate=SHORT_ENTRY, long_entry_rate=LONG_ENTRY, max_funding_age=max_age
    )


def _decide(policy, as_of_time):
    return policy.target_position(PolicyContext(as_of_time=as_of_time, candles=()))


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


def _candle_store(path, prices=None, hours=26):
    prices = prices or ["100"] * hours
    store = SQLiteHistoricalCandleStore(path)
    store.write_batch(
        [
            HistoricalCandle(
                exchange=EXCHANGE,
                market_type=MARKET_TYPE,
                candle=Candle(
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    open_time=T0 + HOUR * index,
                    open=Decimal(price),
                    high=Decimal(price),
                    low=Decimal(price),
                    close=Decimal(price),
                    volume=Decimal(1),
                ),
            )
            for index, price in enumerate(prices)
        ]
    )
    return store


def _funding_store(path, events, *, covered_end=COVERAGE_END):
    store = SQLiteHistoricalFundingStore(path)
    store.write_ingestion_batch(
        [
            HistoricalFundingEvent(
                exchange=EXCHANGE, market_type=MARKET_TYPE, symbol=SYMBOL, funding=event
            )
            for event in events
        ],
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        covered_start=T0,
        covered_end=covered_end,
    )
    return store


def _carry_candidate(candidate_id="carry", *, lag=timedelta(0)):
    return funding_carry_candidate(
        candidate_id,
        short_entry_rate=SHORT_ENTRY,
        long_entry_rate=LONG_ENTRY,
        max_funding_age=MAX_AGE,
        publication_lag=lag,
    )


def _evaluate(tmp_path, events, candidate, *, windows=(FIRST_WINDOW,), cost_model=None, name="a"):
    candles = _candle_store(tmp_path / f"candles_{name}.db")
    funding = _funding_store(tmp_path / f"funding_{name}.db", events)
    try:
        history = load_funding_signal_history(
            funding,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            coverage_start=T0,
            coverage_end=COVERAGE_END,
            publication_lag=timedelta(
                microseconds=dict(candidate.parameters).get("publication_lag_us", 0)
            ),
        )
        return evaluate_funding_research_candidate(
            candles,
            funding,
            history,
            candidate,
            windows=windows,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=CONFIG,
            cost_model=cost_model or ZeroCostModel(),
            funding_model=LinearFundingModel(),
        )
    finally:
        candles.close()
        funding.close()


# ================================================================
# temporal availability of the signal
# ================================================================


def test_settled_event_is_visible_exactly_at_its_availability_boundary():
    history = _history([_event(8, "0.001")], lag=timedelta(minutes=30))
    boundary = T0 + HOUR * 8 + timedelta(minutes=30)
    assert history.latest_settled_at(boundary).funding_rate == Decimal("0.001")
    assert history.latest_settled_at(boundary - timedelta(microseconds=1)) is None


def test_zero_publication_lag_makes_the_event_visible_at_settlement():
    history = _history([_event(8, "0.001")])
    assert history.latest_settled_at(T0 + HOUR * 8).funding_rate == Decimal("0.001")
    assert history.latest_settled_at(T0 + HOUR * 8 - timedelta(microseconds=1)) is None


def test_late_publication_is_not_visible_early():
    history = _history([_event(0, "0.001"), _event(8, "0.002")], lag=timedelta(hours=2))
    # at 09:00 the 08:00 settlement is not yet published; 00:00 is the latest known
    assert history.latest_settled_at(T0 + HOUR * 9).funding_rate == Decimal("0.001")
    assert history.latest_settled_at(T0 + HOUR * 10).funding_rate == Decimal("0.002")


def test_knowledge_cutoff_outside_coverage_is_an_error_not_a_guess():
    history = _history([_event(8, "0.001")], start=T0 + HOUR, lag=timedelta(hours=1))
    _raises_exactly(
        ValueError,
        f"knowledge cutoff for as_of_time={T0 + HOUR * 2 - timedelta(microseconds=1)!r} is "
        f"before funding coverage start {T0 + HOUR!r}",
        history.latest_settled_at,
        T0 + HOUR * 2 - timedelta(microseconds=1),
    )
    _raises_exactly(
        ValueError,
        f"knowledge cutoff for as_of_time={COVERAGE_END + HOUR!r} is not before funding "
        f"coverage end {COVERAGE_END!r}",
        history.latest_settled_at,
        COVERAGE_END + HOUR,
    )


def test_future_funding_changes_do_not_change_earlier_decisions():
    base = [_event(0, "0.001"), _event(8, "0.0015"), _event(16, "-0.002")]
    altered = [_event(0, "0.001"), _event(8, "0.0015"), _event(16, "0.009"), _event(20, "-1")]
    first, second = _policy(_history(base)), _policy(_history(altered))
    for hour in range(1, 16):
        as_of = T0 + HOUR * hour
        assert _decide(first, as_of) == _decide(second, as_of)
    assert _decide(first, T0 + HOUR * 16) == PositionTarget.LONG
    assert _decide(second, T0 + HOUR * 16) == PositionTarget.SHORT


def test_future_funding_after_the_window_does_not_change_the_backtest(tmp_path):
    base = [_event(0, "0.001"), _event(8, "0.0015"), _event(16, "0.001")]
    altered = [_event(0, "0.001"), _event(8, "0.0015"), _event(16, "-0.5")]
    first = _evaluate(tmp_path, base, _carry_candidate(), name="base")
    second = _evaluate(tmp_path, altered, _carry_candidate(), name="altered")
    assert first.results == second.results


# ================================================================
# history data rules: duplicates, order, coverage, quality gate
# ================================================================


def test_history_rejects_unordered_and_duplicate_events():
    _raises_exactly(
        ValueError,
        f"events[1].event_time ({T0!r}) must be strictly after events[0].event_time; "
        "duplicate or unordered funding events are not a single unambiguous signal",
        _history,
        [_event(8, "0.001"), _event(0, "0.001")],
    )
    with pytest.raises(ValueError, match="duplicate or unordered"):
        _history([_event(8, "0.001"), _event(8, "-0.001", rate_type="Special")])


def test_history_rejects_events_outside_coverage_and_bad_arguments():
    with pytest.raises(ValueError, match="is outside coverage"):
        _history([_event(30, "0.001")])
    _raises_exactly(
        ValueError, "publication_lag must be >= 0, got datetime.timedelta(days=-1, seconds=86399)",
        _history, [], lag=timedelta(seconds=-1),
    )  # fmt: skip
    _raises_exactly(TypeError, "events must be a tuple, got list", FundingSignalHistory,
                    exchange=EXCHANGE, market_type=MARKET_TYPE, symbol=SYMBOL, coverage_start=T0,
                    coverage_end=COVERAGE_END, publication_lag=timedelta(0), events=[])  # fmt: skip


def test_history_is_immutable_and_exposes_no_raw_events():
    history = _history([_event(0, "0.001")])
    with pytest.raises(AttributeError):
        history.publication_lag = timedelta(hours=1)
    public = {name for name in dir(history) if not name.startswith("_")}
    assert public == {
        "exchange",
        "market_type",
        "symbol",
        "coverage_start",
        "coverage_end",
        "publication_lag",
        "latest_settled_at",
    }


def test_loader_fails_closed_on_a_coverage_gap(tmp_path):
    funding = _funding_store(tmp_path / "gap.db", [_event(0, "0.001")], covered_end=T0 + HOUR * 10)
    try:
        with pytest.raises(ValueError, match="funding signal history quality gate failed"):
            load_funding_signal_history(
                funding,
                exchange=EXCHANGE,
                market_type=MARKET_TYPE,
                symbol=SYMBOL,
                coverage_start=T0,
                coverage_end=COVERAGE_END,
                publication_lag=timedelta(0),
            )
    finally:
        funding.close()


def test_loader_rejects_ambiguous_same_instant_rate_types(tmp_path):
    events = [_event(8, "0.001"), _event(8, "-0.0005", rate_type="Special")]
    funding = _funding_store(tmp_path / "special.db", events)
    try:
        with pytest.raises(ValueError, match="duplicate or unordered"):
            load_funding_signal_history(
                funding,
                exchange=EXCHANGE,
                market_type=MARKET_TYPE,
                symbol=SYMBOL,
                coverage_start=T0,
                coverage_end=COVERAGE_END,
                publication_lag=timedelta(0),
            )
    finally:
        funding.close()


# ================================================================
# policy rules: thresholds, neutral band, missing/stale data
# ================================================================


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        ("0.0005", PositionTarget.SHORT),  # boundary: at short_entry_rate
        ("0.003", PositionTarget.SHORT),
        ("0.0004", PositionTarget.FLAT),
        ("0", PositionTarget.FLAT),
        ("-0.0004", PositionTarget.FLAT),
        ("-0.0005", PositionTarget.LONG),  # boundary: at long_entry_rate
        ("-0.01", PositionTarget.LONG),
    ],
)
def test_threshold_rules(rate, expected):
    assert _decide(_policy(_history([_event(0, rate)])), T0 + HOUR) == expected


def test_missing_rate_is_never_treated_as_zero():
    # with long_entry_rate = +0.0001, a rate of 0 would mean LONG; no event must mean FLAT
    history = _history([_event(8, "0.001")])
    policy = FundingCarryPolicy(
        history,
        short_entry_rate=Decimal("0.0005"),
        long_entry_rate=Decimal("0.0001"),
        max_funding_age=MAX_AGE,
    )
    assert _decide(policy, T0 + HOUR * 7) == PositionTarget.FLAT


def test_stale_rate_leads_to_no_trade():
    policy = _policy(_history([_event(0, "0.001")]), max_age=timedelta(hours=2))
    assert _decide(policy, T0 + HOUR * 2) == PositionTarget.SHORT  # age == max age
    assert _decide(policy, T0 + HOUR * 2 + timedelta(microseconds=1)) == PositionTarget.FLAT


def test_policy_rejects_invalid_thresholds_and_foreign_symbol():
    history = _history([_event(0, "0.001")])
    _raises_exactly(
        ValueError,
        "long_entry_rate must be strictly below short_entry_rate, got "
        "long_entry_rate=0.001, short_entry_rate=0.001",
        FundingCarryPolicy,
        history,
        short_entry_rate=Decimal("0.001"),
        long_entry_rate=Decimal("0.001"),
        max_funding_age=MAX_AGE,
    )
    with pytest.raises(TypeError, match="short_entry_rate must be a Decimal"):
        FundingCarryPolicy(
            history, short_entry_rate=0.001, long_entry_rate=LONG_ENTRY, max_funding_age=MAX_AGE
        )
    foreign = Candle(
        symbol="ETHUSDT",
        timeframe=TIMEFRAME,
        open_time=T0,
        open=Decimal(1),
        high=Decimal(1),
        low=Decimal(1),
        close=Decimal(1),
        volume=Decimal(1),
    )
    with pytest.raises(ValueError, match="does not match funding history symbol"):
        _policy(history).target_position(PolicyContext(as_of_time=T0 + HOUR, candles=(foreign,)))


def test_no_trade_control_is_always_flat():
    control = NoTradeControlPolicy()
    for hour in (1, 5, 20):
        assert _decide(control, T0 + HOUR * hour) == PositionTarget.FLAT


# ================================================================
# candidate traceability and policy factory
# ================================================================


def test_candidate_parameters_fully_describe_the_configuration():
    candidate = _carry_candidate(lag=timedelta(minutes=5))
    assert candidate.parameters == (
        ("long_entry_rate", Decimal("-0.0005")),
        ("max_funding_age_us", 9 * 3600 * 1_000_000),
        ("publication_lag_us", 5 * 60 * 1_000_000),
        ("short_entry_rate", Decimal("0.0005")),
        ("strategy", FUNDING_CARRY_STRATEGY),
    )
    assert no_trade_control_candidate("control").parameters == (
        ("strategy", NO_TRADE_CONTROL_STRATEGY),
    )


def test_factory_builds_fresh_policies_from_candidate_parameters():
    history = _history([_event(0, "0.001")])
    factory = funding_research_policy_factory(_carry_candidate(), history)
    first, second = factory(), factory()
    assert first is not second
    assert isinstance(first, FundingCarryPolicy)
    assert _decide(first, T0 + HOUR) == PositionTarget.SHORT
    assert funding_research_policy_factory(no_trade_control_candidate("c"), history)() is not None


def test_factory_rejects_inconsistent_or_unknown_candidates():
    history = _history([_event(0, "0.001")])
    with pytest.raises(ValueError, match="does not match history publication_lag"):
        funding_research_policy_factory(_carry_candidate(lag=timedelta(minutes=1)), history)
    from crypto_quant_lab.validation.candidate import Candidate

    with pytest.raises(ValueError, match="unsupported research strategy parameter"):
        funding_research_policy_factory(
            Candidate(candidate_id="x", parameters=(("strategy", "other"),)), history
        )
    with pytest.raises(ValueError, match="parameters must be exactly"):
        funding_research_policy_factory(
            Candidate(
                candidate_id="x",
                parameters=(("extra", 1), ("strategy", NO_TRADE_CONTROL_STRATEGY)),
            ),
            history,
        )


# ================================================================
# economic behavior through the real SQLite store + rolling runner
# ================================================================

# Constant price 100, window [00:00, 12:00): decision at 01:00 (candle 0 close),
# fill at 01:00 open of candle 1; the 00:00 settlement hits a flat account,
# the 08:00 settlement hits the held position (reference price 100, qty 1).


def test_short_receives_positive_funding_exactly_once(tmp_path):
    trial = _evaluate(tmp_path, [_event(0, "0.001"), _event(8, "0.0015")], _carry_candidate())
    result = trial.results[0].result
    assert result.fill_count == 1
    # short 1 x 100 x 0.0015 = -0.15 cost -> +0.15 cash, applied once
    assert result.final_equity == Decimal("1000.15")


def test_long_receives_negative_funding(tmp_path):
    trial = _evaluate(tmp_path, [_event(0, "-0.001"), _event(8, "-0.002")], _carry_candidate())
    result = trial.results[0].result
    assert result.fill_count == 1
    # long 1 x 100 x -0.002 = -0.2 cost -> +0.2 cash
    assert result.final_equity == Decimal("1000.2")


def test_neutral_band_and_control_never_trade_or_settle_funding(tmp_path):
    neutral = _evaluate(tmp_path, [_event(0, "0.0001"), _event(8, "0.0001")], _carry_candidate(),
                        name="neutral")  # fmt: skip
    control = _evaluate(tmp_path, [_event(0, "0.001"), _event(8, "0.0015")],
                        no_trade_control_candidate("control"), name="control")  # fmt: skip
    for trial in (neutral, control):
        result = trial.results[0].result
        assert result.fill_count == 0
        assert result.final_equity == Decimal(1000)


def test_transaction_costs_are_applied_on_top_of_funding(tmp_path):
    events = [_event(0, "0.001"), _event(8, "0.0015")]
    zero = _evaluate(tmp_path, events, _carry_candidate(), name="zero").results[0].result
    costly = (
        _evaluate(
            tmp_path,
            events,
            _carry_candidate(),
            cost_model=ProportionalCommissionModel(rate=Decimal("0.001")),
            name="costly",
        )
        .results[0]
        .result
    )
    # one fill at notional 100 x 0.001 = 0.1 commission
    assert costly.final_equity == Decimal("1000.05")
    assert zero.final_equity - costly.final_equity == Decimal("0.1")


def test_stale_signal_closes_the_position_before_later_settlements(tmp_path):
    candidate = funding_carry_candidate(
        "short_lived",
        short_entry_rate=SHORT_ENTRY,
        long_entry_rate=LONG_ENTRY,
        max_funding_age=timedelta(hours=3),
        publication_lag=timedelta(0),
    )
    # 00:00 rate is fresh until 03:00 -> SHORT from 01:00 open; stale at 04:00 -> FLAT,
    # closed at 04:00 open. The 08:00 settlement (fresh again, visible at 08:00) re-enters
    # SHORT at the 08:00 open, but the engine settles funding BEFORE the same-instant fill
    # (pre-fill, flat position), so neither settlement is ever paid or received.
    trial = _evaluate(tmp_path, [_event(0, "0.001"), _event(8, "0.5")], candidate)
    result = trial.results[0].result
    assert result.fill_count == 3
    assert result.final_equity == Decimal(1000)


def test_rolling_windows_and_trial_group_metrics_integration(tmp_path):
    events = [_event(0, "0.001"), _event(8, "0.0015"), _event(16, "0.002")]
    windows = (FIRST_WINDOW, SECOND_WINDOW)
    carry = _evaluate(tmp_path, events, _carry_candidate(), windows=windows, name="carry")
    control = _evaluate(tmp_path, events, no_trade_control_candidate("control"), windows=windows,
                        name="control")  # fmt: skip
    group = TrialGroup(group_id="funding-carry-first-slice", trials=(carry, control))
    assert recorded_trial_count(group) == 2
    first, second = (window_result.result for window_result in carry.results)
    # window 2 re-enters SHORT at 13:00 and receives 1 x 100 x 0.002 at 16:00
    assert first.final_equity == Decimal("1000.15")
    assert second.final_equity == Decimal("1000.2")
    assert compute_stage1_metrics(second).total_return == Decimal("0.0002")
    assert compute_stage2_metrics(second).sharpe_ratio > 0
    assert compute_annualized_sharpe_ratio(second, timeframe=TIMEFRAME) > 0
    for window_result in control.results:
        assert compute_stage1_metrics(window_result.result).total_return == Decimal(0)


def test_context_period_never_opens_a_position_before_evaluation_start(tmp_path):
    candles = _candle_store(tmp_path / "ctx_candles.db")
    funding = _funding_store(tmp_path / "ctx_funding.db", [_event(0, "0.001"), _event(8, "0.0015")])
    try:
        history = load_funding_signal_history(
            funding, exchange=EXCHANGE, market_type=MARKET_TYPE, symbol=SYMBOL,
            coverage_start=T0, coverage_end=COVERAGE_END, publication_lag=timedelta(0),
        )  # fmt: skip
        factory = funding_research_policy_factory(_carry_candidate(), history)
        evaluation = TemporalWindow(start=T0 + HOUR * 4, end=T0 + HOUR * 12)
        (window_result,) = run_context_aware_rolling_backtest_from_store(
            candles,
            (ContextAwareWindow(context_start=T0, evaluation=evaluation),),
            policy_factory=factory,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=CONFIG,
            cost_model=ZeroCostModel(),
            funding_required=True,
            funding_store=funding,
            funding_model=LinearFundingModel(),
        )
    finally:
        candles.close()
        funding.close()
    result = window_result.result
    assert result.equity_curve[0].time == evaluation.start + HOUR
    assert result.equity_curve[0].equity == Decimal(1000)  # no position carried in
    assert result.fill_count == 1
    assert result.final_equity == Decimal("1000.15")


def test_evaluation_is_deterministic_and_does_not_mutate_inputs(tmp_path):
    candidate = _carry_candidate()
    snapshot = (candidate, hash(candidate))
    events = [_event(0, "0.001"), _event(8, "0.0015")]
    first = _evaluate(tmp_path, events, candidate, name="one")
    second = _evaluate(tmp_path, events, candidate, name="two")
    assert first == second
    assert (candidate, hash(candidate)) == snapshot


# ================================================================
# scope
# ================================================================


def test_engine_modules_do_not_import_the_research_layer():
    for module in (replay_module, store_runner_module, rolling_module):
        assert "research" not in inspect.getsource(module).split('"""', 2)[-1]


def test_research_module_does_not_touch_cash_or_private_engine_helpers():
    source = inspect.getsource(funding_carry_module)
    for forbidden in (
        "apply_cash_cost",
        "calculate_funding_cost(",
        "_query_canonical_funding_events",
        "import random",
        "urllib",
        "requests",
    ):
        assert forbidden not in source
