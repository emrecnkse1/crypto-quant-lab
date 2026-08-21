import gc
import weakref
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import BacktestConfig, BacktestResult, PositionTarget
from crypto_quant_lab.backtest.store_runner import run_backtest_from_store
from crypto_quant_lab.funding.calculator import LinearFundingModel
from crypto_quant_lab.funding.models import FundingCoverageInterval
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.metrics import (
    compute_periodic_returns,
    compute_stage1_metrics,
    compute_stage2_metrics,
)
from crypto_quant_lab.validation.rolling import (
    ContextAwareWindow,
    WindowResult,
    run_context_aware_rolling_backtest_from_store,
    run_rolling_backtest_from_store,
)
from crypto_quant_lab.validation.windows import TemporalSplit, TemporalWindow


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"

AS_OF_TIME = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)

W0 = TemporalWindow(
    start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
)
W1 = TemporalWindow(
    start=datetime(2024, 1, 1, 10, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
)
W2 = TemporalWindow(
    start=datetime(2024, 1, 1, 12, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 14, 0, tzinfo=UTC)
)
W3 = TemporalWindow(
    start=datetime(2024, 1, 1, 14, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC)
)

_VALID_RESULT = BacktestResult(
    initial_cash=Decimal(1000),
    final_cash=Decimal(1000),
    final_equity=Decimal(1000),
    total_realized_pnl=Decimal(0),
    total_unrealized_pnl=Decimal(0),
    total_pnl=Decimal(0),
    total_cost=Decimal(0),
    fill_count=0,
    trade_count=0,
    equity_curve=(),
)


class _RecordingPolicy:
    def __init__(self, target_fn):
        self._target_fn = target_fn
        self.contexts = []

    def target_position(self, context):
        self.contexts.append(context)
        return self._target_fn(context)


class _PoisonCandleStore:
    def query(self, *args, **kwargs):
        raise AssertionError("candle store must not be queried before upfront validation")

    def write_batch(self, records):
        raise AssertionError("write_batch must never be called")

    def close(self):
        pass


class _CountingCandleStore:
    """Wraps a real store, recording `query()` calls without altering behavior."""

    def __init__(self, inner):
        self._inner = inner
        self.query_calls: list[dict] = []

    def query(self, exchange, market_type, symbol, timeframe, start_time, end_time):
        self.query_calls.append(
            {
                "exchange": exchange,
                "market_type": market_type,
                "symbol": symbol,
                "timeframe": timeframe,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        return self._inner.query(exchange, market_type, symbol, timeframe, start_time, end_time)

    def write_batch(self, records):
        return self._inner.write_batch(records)

    def close(self):
        return self._inner.close()


class _FakeFundingStore:
    def __init__(self, *, coverage=(), events=()):
        self._coverage = coverage
        self._events = events

    def query_events(self, *, exchange, market_type, symbol, start_time, end_time):
        return self._events

    def query_coverage(self, *, exchange, market_type, symbol, start_time, end_time):
        return self._coverage

    def close(self):
        pass


def _make_record(open_time, *, price=Decimal(100)):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _candle_store(tmp_path, hours=range(8, 16)):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    store.write_batch([_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in hours])
    return store


def _config(initial_cash=Decimal(1000), position_quantity=Decimal(1)):
    return BacktestConfig(initial_cash=initial_cash, position_quantity=position_quantity)


def _flat_policy():
    return _RecordingPolicy(lambda context: PositionTarget.FLAT)


def _never_called_factory():
    raise AssertionError("policy_factory must not be called when upfront validation fails")


def _coverage(start, end):
    return FundingCoverageInterval(start_time=start, end_time=end)


def _query_boundaries(counting_store):
    """Distinct `(start_time, end_time)` pairs across all recorded `store.query()` calls.

    Deliberately count-agnostic: `prepare_backtest_dataset` may perform any
    number of internal reads per window (today: a quality-then-dataset
    double-read) — that internal read count is Layer-1's own implementation
    detail, not this orchestrator's contract. Collapsing to a boundary set
    still proves exact per-window boundary forwarding and exact
    executed/not-executed membership, without coupling to that count.
    """
    return {(call["start_time"], call["end_time"]) for call in counting_store.query_calls}


def _rolling(
    store,
    windows,
    *,
    policy_factory,
    funding_required=False,
    funding_store=None,
    funding_model=None,
    as_of_time=AS_OF_TIME,
    cost_model=None,
):
    return run_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=policy_factory,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=as_of_time,
        config=_config(),
        cost_model=cost_model or ZeroCostModel(),
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )


def _context_rolling(
    store,
    windows,
    *,
    policy_factory,
    funding_required=False,
    funding_store=None,
    funding_model=None,
    as_of_time=AS_OF_TIME,
    cost_model=None,
):
    return run_context_aware_rolling_backtest_from_store(
        store,
        windows,
        policy_factory=policy_factory,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=as_of_time,
        config=_config(),
        cost_model=cost_model or ZeroCostModel(),
        funding_required=funding_required,
        funding_store=funding_store,
        funding_model=funding_model,
    )


# --- WindowResult: shape / validation ---


def test_window_result_accepts_valid_fields():
    result = WindowResult(window=W0, result=_VALID_RESULT)
    assert result.window == W0
    assert result.result == _VALID_RESULT


def test_window_result_rejects_invalid_window():
    with pytest.raises(TypeError, match="window"):
        WindowResult(window="not a window", result=_VALID_RESULT)


def test_window_result_rejects_invalid_result():
    with pytest.raises(TypeError, match="result"):
        WindowResult(window=W0, result="not a result")


def test_window_result_equality_is_value_based():
    a = WindowResult(window=W0, result=_VALID_RESULT)
    b = WindowResult(window=W0, result=_VALID_RESULT)
    assert a == b


def test_window_result_is_frozen():
    result = WindowResult(window=W0, result=_VALID_RESULT)
    with pytest.raises(FrozenInstanceError):
        result.window = W1


# --- input validation: windows container ---


def test_empty_windows_returns_empty_tuple():
    result = _rolling(_PoisonCandleStore(), (), policy_factory=_never_called_factory)
    assert result == ()


def test_list_windows_is_rejected_before_activity():
    with pytest.raises(TypeError, match="windows must be a tuple"):
        _rolling(_PoisonCandleStore(), [W0], policy_factory=_never_called_factory)


def test_generator_windows_is_rejected_before_activity():
    def gen():
        yield W0

    with pytest.raises(TypeError, match="windows must be a tuple"):
        _rolling(_PoisonCandleStore(), gen(), policy_factory=_never_called_factory)


def test_invalid_window_element_at_index_0_is_rejected_before_activity():
    with pytest.raises(TypeError, match=r"windows\[0\]"):
        _rolling(_PoisonCandleStore(), ("not a window", W1), policy_factory=_never_called_factory)


def test_invalid_window_element_at_later_index_is_detected_upfront():
    with pytest.raises(TypeError, match=r"windows\[1\]"):
        _rolling(_PoisonCandleStore(), (W0, "not a window"), policy_factory=_never_called_factory)


def test_temporal_split_element_is_rejected_before_activity():
    # TemporalSplit is a distinct, non-repurposed concept (VALIDATION_SPEC.md
    # Bölüm 8.3.12) — passing one as a `windows` element must be rejected by
    # the same upfront TemporalWindow-only validation, not silently accepted.
    split = TemporalSplit(in_sample=W0, out_of_sample=W1, timeframe=TIMEFRAME)
    with pytest.raises(TypeError, match=r"windows\[0\]"):
        _rolling(_PoisonCandleStore(), (split,), policy_factory=_never_called_factory)


def test_non_callable_policy_factory_is_rejected_before_store_io():
    with pytest.raises(TypeError, match="policy_factory must be callable"):
        _rolling(_PoisonCandleStore(), (W0,), policy_factory="not callable")


# --- execution and boundaries ---


def test_single_window_produces_one_window_result(tmp_path):
    store = _candle_store(tmp_path)
    result = _rolling(store, (W0,), policy_factory=_flat_policy)
    assert len(result) == 1
    assert result[0].window == W0
    assert isinstance(result[0].result, BacktestResult)


def test_multiple_windows_preserve_exact_input_order(tmp_path):
    store = _candle_store(tmp_path)
    windows = (W2, W0, W1)  # deliberately not chronological
    result = _rolling(store, windows, policy_factory=_flat_policy)
    assert tuple(r.window for r in result) == windows


def test_duplicate_equal_windows_are_not_deduplicated(tmp_path):
    store = _candle_store(tmp_path)
    windows = (W0, W0)
    result = _rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2
    assert result[0].window == W0
    assert result[1].window == W0


def test_overlapping_windows_are_not_sorted_clipped_merged_or_rejected(tmp_path):
    store = _candle_store(tmp_path)
    overlapping = TemporalWindow(
        start=datetime(2024, 1, 1, 9, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 11, 0, tzinfo=UTC)
    )
    windows = (W0, overlapping)
    result = _rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2
    assert result[0].window == W0
    assert result[1].window == overlapping


def test_forwards_exact_time_boundaries_per_window(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (W0, W1)
    _rolling(counting_store, windows, policy_factory=_flat_policy)

    assert len(counting_store.query_calls) > 0
    assert _query_boundaries(counting_store) == {(W0.start, W0.end), (W1.start, W1.end)}

    # canonical window order is preserved regardless of how many internal
    # reads each window took: every W0-boundary call precedes every
    # W1-boundary call.
    call_boundaries = [(c["start_time"], c["end_time"]) for c in counting_store.query_calls]
    last_w0_index = max(i for i, b in enumerate(call_boundaries) if b == (W0.start, W0.end))
    first_w1_index = min(i for i, b in enumerate(call_boundaries) if b == (W1.start, W1.end))
    assert last_w0_index < first_w1_index


def test_zero_context_all_window_candles_are_evaluation_candles(tmp_path):
    store = _candle_store(tmp_path)
    result = _rolling(store, (W0,), policy_factory=_flat_policy)
    assert len(result[0].result.equity_curve) == 2  # both candles in [8:00, 10:00) are evaluation


def test_funding_required_flows_through_canonical_composition(tmp_path):
    store = _candle_store(tmp_path)
    # A single interval spanning both windows fully covers each window's own
    # sub-range once queried independently (the fake store returns it
    # unfiltered regardless of the requested range).
    funding_store = _FakeFundingStore(coverage=[_coverage(W0.start, W1.end)], events=[])
    result = _rolling(
        store,
        (W0, W1),
        policy_factory=_flat_policy,
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    assert len(result) == 2
    assert all(r.result.total_cost == Decimal(0) for r in result)


# --- freshness ---


def test_exactly_one_factory_call_per_window_in_order(tmp_path):
    store = _candle_store(tmp_path)
    windows = (W0, W1, W2)
    call_order = []

    def factory():
        call_order.append(len(call_order))
        return _flat_policy()

    _rolling(store, windows, policy_factory=factory)
    assert call_order == [0, 1, 2]


def test_distinct_but_equality_equal_policy_instances_are_accepted(tmp_path):
    @dataclass
    class _EqualPolicy:
        tag: str = "same"

        def target_position(self, context):
            return PositionTarget.FLAT

    store = _candle_store(tmp_path)
    result = _rolling(store, (W0, W1), policy_factory=_EqualPolicy)
    assert len(result) == 2
    assert _EqualPolicy() == _EqualPolicy()
    assert _EqualPolicy() is not _EqualPolicy()


def test_same_object_factory_output_is_rejected_before_affected_window_runs(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    shared_policy = _flat_policy()

    def factory():
        return shared_policy

    with pytest.raises(ValueError, match=r"windows\[1\]"):
        _rolling(counting_store, (W0, W1), policy_factory=factory)

    # only window 0 executed; window 1 (the affected window) has no query at all
    assert _query_boundaries(counting_store) == {(W0.start, W0.end)}


def test_invalid_factory_output_is_rejected_before_affected_window_runs(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    outputs = iter([_flat_policy(), object()])

    def factory():
        return next(outputs)

    with pytest.raises(TypeError, match=r"windows\[1\].*target_position"):
        _rolling(counting_store, (W0, W1), policy_factory=factory)

    # only window 0 executed; window 1 (the affected window) has no query at all
    assert _query_boundaries(counting_store) == {(W0.start, W0.end)}


def test_factory_exception_propagates_as_original_object(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))

    class _CustomFactoryError(Exception):
        pass

    expected_exception = _CustomFactoryError("boom")
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _flat_policy()
        raise expected_exception

    with pytest.raises(_CustomFactoryError) as excinfo:
        _rolling(counting_store, (W0, W1), policy_factory=factory)

    # not merely the same type/message — the exact same exception object,
    # proving no wrap/re-raise/translation happened
    assert excinfo.value is expected_exception

    # window 0 executed; window 1 (the affected window, whose factory call
    # raised) has no query at all
    assert _query_boundaries(counting_store) == {(W0.start, W0.end)}


def test_stateful_fixture_shows_zero_cross_window_state_carryover(tmp_path):
    class _CountingPolicy:
        def __init__(self):
            self.calls = 0

        def target_position(self, context):
            self.calls += 1
            return PositionTarget.FLAT

    store = _candle_store(tmp_path)
    windows = (W0, W1, W2)
    created = []

    def factory():
        policy = _CountingPolicy()
        created.append(policy)
        return policy

    _rolling(store, windows, policy_factory=factory)

    assert [p.calls for p in created] == [2, 2, 2]  # each window's policy starts fresh


def test_prior_accepted_policies_remain_strongly_retained_throughout_orchestration(tmp_path):
    """Every earlier accepted policy instance must survive a forced GC pass.

    Uses `weakref.ref` — never a strong reference — to observe each prior
    instance from the test side. A hypothetical implementation that retained
    only bare `id(policy)` ints (or only the immediately previous loop
    variable) would let an earlier instance become collectible once the
    orchestrator itself no longer references it; a forced `gc.collect()`
    partway through orchestration would then free it, and its weakref would
    go dead. With 4 windows, by the time window 2's factory call happens the
    orchestrator's own loop variable has already moved on twice, so window
    0's liveness at that point depends entirely on the orchestrator's own
    retention — not on any accidental protection from a still-live local.
    """
    store = _candle_store(tmp_path)
    windows = (W0, W1, W2, W3)

    weak_refs: list[weakref.ReferenceType] = []
    call_count = 0

    def factory():
        nonlocal call_count
        gc.collect()
        for index, ref in enumerate(weak_refs):
            assert ref() is not None, (
                f"policy from windows[{index}] was garbage collected before orchestration "
                "finished — it was not strongly retained"
            )
        call_count += 1
        policy = _flat_policy()  # a fresh, weak-referenceable instance every call
        weak_refs.append(weakref.ref(policy))
        return policy

    result = _rolling(store, windows, policy_factory=factory)

    # every prior instance's liveness was already checked, while orchestration
    # was still in progress, inside `factory()` itself above — retention is
    # only contractually required for the duration of orchestration (Bölüm
    # 8.3.6), not after `run_rolling_backtest_from_store` has returned, so no
    # post-return liveness assertion is made here.
    assert len(result) == 4
    assert call_count == 4


# --- fail-fast / partial-execution boundary ---


def test_earlier_windows_execute_and_no_subsequent_window_executes_on_failure(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (W0, W1, W2, W3)
    made = []

    def factory():
        if len(made) < 2:
            policy = _flat_policy()
        else:
            policy = made[1]  # reuse window 1's instance starting at window 2
        made.append(policy)
        return policy

    with pytest.raises(ValueError, match=r"windows\[2\]"):
        _rolling(counting_store, windows, policy_factory=factory)

    # windows 0 and 1 (earlier) each have at least one query; window 2
    # (affected) and window 3 (later) have none
    assert _query_boundaries(counting_store) == {(W0.start, W0.end), (W1.start, W1.end)}


# --- canonical-engine preservation ---


def test_rolling_output_matches_direct_per_window_composition(tmp_path):
    store = _candle_store(tmp_path)
    windows = (W0, W1, W2)

    class _FixedPolicy:
        def target_position(self, context):
            return PositionTarget.FLAT

    rolling_result = _rolling(store, windows, policy_factory=_FixedPolicy)

    direct_results = tuple(
        run_backtest_from_store(
            store,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            requested_start=w.start,
            requested_end=w.end,
            as_of_time=AS_OF_TIME,
            config=_config(),
            policy=_FixedPolicy(),
            cost_model=ZeroCostModel(),
            evaluation_start=w.start,
        )
        for w in windows
    )

    assert tuple(r.result for r in rolling_result) == direct_results


# =====================================================================
# ContextAwareWindow: value-object tests (VALIDATION_SPEC.md Bölüm 8.3.16)
# =====================================================================


def test_context_aware_window_accepts_valid_non_zero_context():
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    assert cw.context_start == W0.start
    assert cw.evaluation == W1


def test_context_aware_window_accepts_zero_context_equality_boundary():
    cw = ContextAwareWindow(context_start=W1.start, evaluation=W1)
    assert cw.context_start == W1.start == cw.evaluation.start


def test_context_aware_window_field_preservation():
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W2)
    assert cw.context_start == W0.start
    assert cw.evaluation is W2


def test_context_aware_window_value_equality():
    a = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    b = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    assert a == b


def test_context_aware_window_hashable():
    a = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    b = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_context_aware_window_is_frozen():
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    with pytest.raises(FrozenInstanceError):
        cw.context_start = W0.end


def test_context_aware_window_is_slotted():
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    assert not hasattr(cw, "__dict__")


def test_context_aware_window_rejects_wrong_evaluation_type():
    with pytest.raises(TypeError, match="evaluation"):
        ContextAwareWindow(context_start=W0.start, evaluation="not a window")


def test_context_aware_window_rejects_temporal_split_as_evaluation():
    split = TemporalSplit(in_sample=W0, out_of_sample=W1, timeframe=TIMEFRAME)
    with pytest.raises(TypeError, match="evaluation"):
        ContextAwareWindow(context_start=W0.start, evaluation=split)


def test_context_aware_window_rejects_non_datetime_context_start():
    with pytest.raises(TypeError, match="datetime"):
        ContextAwareWindow(context_start="not a datetime", evaluation=W1)


def test_context_aware_window_rejects_naive_context_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        ContextAwareWindow(context_start=datetime(2024, 1, 1, 8, 0), evaluation=W1)  # noqa: DTZ001


def test_context_aware_window_rejects_pseudo_naive_context_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        ContextAwareWindow(
            context_start=datetime(2024, 1, 1, 8, 0, tzinfo=_BrokenTzInfo()), evaluation=W1
        )


def test_context_aware_window_rejects_context_start_after_evaluation_start():
    with pytest.raises(ValueError, match="context_start must not be after evaluation.start"):
        ContextAwareWindow(context_start=W1.end, evaluation=W1)


def test_context_aware_window_does_not_mutate_or_replace_evaluation():
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    assert cw.evaluation is W1
    assert W1.start == datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
    assert W1.end == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_context_aware_window_not_exported_from_package_root():
    import crypto_quant_lab.validation as validation_package

    assert not hasattr(validation_package, "ContextAwareWindow")
    assert not hasattr(validation_package, "run_context_aware_rolling_backtest_from_store")


# =====================================================================
# run_context_aware_rolling_backtest_from_store: top-level / fail-fast
# =====================================================================


def test_context_aware_empty_windows_returns_empty_tuple():
    result = _context_rolling(_PoisonCandleStore(), (), policy_factory=_never_called_factory)
    assert result == ()


def test_context_aware_non_tuple_windows_is_rejected_before_activity():
    with pytest.raises(TypeError, match="windows must be a tuple"):
        _context_rolling(
            _PoisonCandleStore(),
            [ContextAwareWindow(context_start=W0.start, evaluation=W1)],
            policy_factory=_never_called_factory,
        )


def test_context_aware_invalid_member_at_index_0_is_rejected_before_activity():
    with pytest.raises(TypeError, match=r"windows\[0\]"):
        _context_rolling(
            _PoisonCandleStore(),
            ("not a ContextAwareWindow", ContextAwareWindow(context_start=W1.start, evaluation=W1)),
            policy_factory=_never_called_factory,
        )


def test_context_aware_invalid_member_at_later_index_is_detected_upfront():
    with pytest.raises(TypeError, match=r"windows\[1\]"):
        _context_rolling(
            _PoisonCandleStore(),
            (ContextAwareWindow(context_start=W0.start, evaluation=W1), "not a ContextAwareWindow"),
            policy_factory=_never_called_factory,
        )


def test_context_aware_plain_temporal_window_element_is_rejected():
    # a plain TemporalWindow (the zero-context runner's own element type) is
    # not silently accepted by the context-aware runner.
    with pytest.raises(TypeError, match=r"windows\[0\]"):
        _context_rolling(_PoisonCandleStore(), (W0,), policy_factory=_never_called_factory)


def test_context_aware_full_member_validation_precedes_policy_factory_validation():
    # an invalid element at a later index still fails on that element first,
    # even though policy_factory is also invalid — proving full-tuple member
    # validation runs before policy_factory is ever inspected.
    with pytest.raises(TypeError, match=r"windows\[1\]"):
        _context_rolling(
            _PoisonCandleStore(),
            (ContextAwareWindow(context_start=W0.start, evaluation=W1), "not a ContextAwareWindow"),
            policy_factory="not callable",
        )


def test_context_aware_non_callable_policy_factory_is_rejected_before_store_io():
    with pytest.raises(TypeError, match="policy_factory must be callable"):
        _context_rolling(
            _PoisonCandleStore(),
            (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
            policy_factory="not callable",
        )


def test_context_aware_invalid_window_causes_zero_factory_calls():
    calls = []

    def factory():
        calls.append(1)
        return _flat_policy()

    with pytest.raises(TypeError):
        _context_rolling(_PoisonCandleStore(), ("not a window",), policy_factory=factory)
    assert calls == []


# =====================================================================
# run_context_aware_rolling_backtest_from_store: policy freshness/failure
# =====================================================================


def test_context_aware_exactly_one_factory_call_per_window_in_order(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
        ContextAwareWindow(context_start=W2.start, evaluation=W3),
    )
    call_order = []

    def factory():
        call_order.append(len(call_order))
        return _flat_policy()

    _context_rolling(store, windows, policy_factory=factory)
    assert call_order == [0, 1, 2]


def test_context_aware_distinct_but_equality_equal_policy_instances_are_accepted(tmp_path):
    @dataclass
    class _EqualPolicy:
        tag: str = "same"

        def target_position(self, context):
            return PositionTarget.FLAT

    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    result = _context_rolling(store, windows, policy_factory=_EqualPolicy)
    assert len(result) == 2


def test_context_aware_same_object_factory_output_is_rejected_before_affected_window_runs(
    tmp_path,
):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    shared_policy = _flat_policy()
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )

    def factory():
        return shared_policy

    with pytest.raises(ValueError, match=r"windows\[1\]"):
        _context_rolling(counting_store, windows, policy_factory=factory)

    assert _query_boundaries(counting_store) == {(W0.start, W1.end)}


def test_context_aware_invalid_factory_output_is_rejected_before_affected_window_runs(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    outputs = iter([_flat_policy(), object()])

    def factory():
        return next(outputs)

    with pytest.raises(TypeError, match=r"windows\[1\].*target_position"):
        _context_rolling(counting_store, windows, policy_factory=factory)

    assert _query_boundaries(counting_store) == {(W0.start, W1.end)}


def test_context_aware_factory_exception_propagates_as_original_object(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )

    class _CustomFactoryError(Exception):
        pass

    expected_exception = _CustomFactoryError("boom")
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _flat_policy()
        raise expected_exception

    with pytest.raises(_CustomFactoryError) as excinfo:
        _context_rolling(counting_store, windows, policy_factory=factory)

    assert excinfo.value is expected_exception
    assert _query_boundaries(counting_store) == {(W0.start, W1.end)}


def test_context_aware_earlier_windows_execute_and_no_subsequent_window_executes_on_failure(
    tmp_path,
):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
        ContextAwareWindow(context_start=W2.start, evaluation=W3),
    )
    made = []

    def factory():
        if len(made) < 1:
            policy = _flat_policy()
        else:
            policy = made[0]  # reuse window 0's instance starting at window 1
        made.append(policy)
        return policy

    with pytest.raises(ValueError, match=r"windows\[1\]"):
        _context_rolling(counting_store, windows, policy_factory=factory)

    # only window 0 (earlier) executed; window 1 (affected) and window 2
    # (later) have no query at all
    assert _query_boundaries(counting_store) == {(W0.start, W1.end)}


def test_context_aware_store_execution_failure_at_later_index_stops_orchestration(tmp_path):
    store = _candle_store(tmp_path, hours=range(8, 12))  # only covers W0/W1, not W2
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),  # requests uncovered data
    )
    with pytest.raises(ValueError):
        _context_rolling(store, windows, policy_factory=_flat_policy)


def test_context_aware_no_partial_result_returned_on_failure(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    shared_policy = _flat_policy()

    def factory():
        return shared_policy

    try:
        _context_rolling(counting_store, windows, policy_factory=factory)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    # no return value exists to inspect — the call raised entirely, which is
    # itself the "no partial tuple" guarantee.


def test_context_aware_prior_accepted_policies_remain_strongly_retained(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
        ContextAwareWindow(context_start=W2.start, evaluation=W3),
    )

    weak_refs: list[weakref.ReferenceType] = []

    def factory():
        gc.collect()
        for index, ref in enumerate(weak_refs):
            assert ref() is not None, (
                f"policy from windows[{index}] was garbage collected before orchestration "
                "finished — it was not strongly retained"
            )
        policy = _flat_policy()
        weak_refs.append(weakref.ref(policy))
        return policy

    result = _context_rolling(store, windows, policy_factory=factory)
    assert len(result) == 3


# =====================================================================
# Context/evaluation boundary behavior
# =====================================================================


def test_context_aware_context_candles_reach_policy_prefix(tmp_path):
    store = _candle_store(tmp_path)
    policy = _RecordingPolicy(lambda context: PositionTarget.FLAT)
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    _context_rolling(store, windows, policy_factory=lambda: policy)
    # W0.start=08:00, evaluation=W1=[10:00,12:00): 2 context candles (08,09)
    # + first evaluation candle (10) = 3 candles in the first policy call.
    assert len(policy.contexts[0].candles) == 3


def test_context_aware_context_candles_never_call_policy(tmp_path):
    store = _candle_store(tmp_path)
    policy = _RecordingPolicy(lambda context: PositionTarget.FLAT)
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    _context_rolling(store, windows, policy_factory=lambda: policy)
    # exactly 2 evaluation candles in W1 -> exactly 2 policy calls, never more
    # (the 2 context candles never trigger a call).
    assert len(policy.contexts) == 2


def test_context_aware_context_produces_no_economic_activity(tmp_path):
    store = _candle_store(tmp_path)
    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
    )
    assert result[0].result.fill_count == 0
    assert result[0].result.total_cost == Decimal(0)
    assert result[0].result.total_realized_pnl == Decimal(0)
    assert result[0].result.final_cash == result[0].result.initial_cash


def test_context_aware_equity_curve_contains_only_evaluation_observations(tmp_path):
    store = _candle_store(tmp_path)
    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
    )
    # W1 has exactly 2 candles -> exactly 2 EquityPoints, never the 2 context
    # candles' worth extra.
    assert len(result[0].result.equity_curve) == 2
    assert result[0].result.equity_curve[0].time > W1.start


def test_context_aware_evaluation_produces_normal_fills(tmp_path):
    store = _candle_store(tmp_path)

    def _target(context):
        return PositionTarget.LONG if len(context.candles) == 3 else PositionTarget.FLAT

    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=lambda: _RecordingPolicy(_target),
    )
    assert result[0].result.fill_count >= 1


def test_context_aware_zero_context_special_case_matches_zero_context_runner(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    store_a = _candle_store(tmp_path / "a")
    store_b = _candle_store(tmp_path / "b")

    class _FixedPolicy:
        def target_position(self, context):
            return PositionTarget.FLAT

    context_aware_result = _context_rolling(
        store_a,
        (ContextAwareWindow(context_start=W1.start, evaluation=W1),),
        policy_factory=_FixedPolicy,
    )
    zero_context_result = _rolling(store_b, (W1,), policy_factory=_FixedPolicy)

    assert context_aware_result[0].result == zero_context_result[0].result
    assert context_aware_result[0].window == zero_context_result[0].window


def test_context_aware_missing_context_coverage_fails_through_layer1(tmp_path):
    store = _candle_store(tmp_path, hours=range(10, 16))  # no candles before 10:00
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)  # context needs 8:00
    with pytest.raises(ValueError):
        _context_rolling(store, windows, policy_factory=_flat_policy)


def test_context_aware_gapped_context_fails_through_canonical_quality_gate(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "gapped.db")
    # 08:00 and 09:00 present, but 09:00-10:00 missing before evaluation
    # candles resume at 10:00 -> a gap strictly inside the context range.
    hours_with_gap = [8, 10, 11]
    store.write_batch(
        [_make_record(datetime(2024, 1, 1, hour, 0, tzinfo=UTC)) for hour in hours_with_gap]
    )
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    with pytest.raises(ValueError):
        _context_rolling(store, windows, policy_factory=_flat_policy)


def test_context_aware_no_second_data_access_path(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    _context_rolling(counting_store, windows, policy_factory=_flat_policy)
    # context and evaluation candles are loaded through exactly one Layer-1
    # request boundary per window: [context_start, evaluation.end).
    assert _query_boundaries(counting_store) == {(W0.start, W1.end)}


# =====================================================================
# Store and funding integration
# =====================================================================


def test_context_aware_candle_request_spans_context_start_to_evaluation_end(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W2),)
    _context_rolling(counting_store, windows, policy_factory=_flat_policy)
    assert _query_boundaries(counting_store) == {(W0.start, W2.end)}


def test_context_aware_funding_required_flows_through_canonical_composition(tmp_path):
    store = _candle_store(tmp_path)
    funding_store = _FakeFundingStore(coverage=[_coverage(W1.start, W2.end)], events=[])
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    result = _context_rolling(
        store,
        windows,
        policy_factory=_flat_policy,
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    assert len(result) == 2
    assert all(r.result.total_cost == Decimal(0) for r in result)


def test_context_aware_no_funding_coverage_required_for_context_only_time(tmp_path):
    store = _candle_store(tmp_path)
    # coverage spans only the evaluation range [W1.start, W1.end) — NOT the
    # context range [W0.start, W1.start) — and must still PASS, proving
    # context-period funding coverage is never required.
    funding_store = _FakeFundingStore(coverage=[_coverage(W1.start, W1.end)], events=[])
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    result = _context_rolling(
        store,
        windows,
        policy_factory=_flat_policy,
        funding_required=True,
        funding_store=funding_store,
        funding_model=LinearFundingModel(),
    )
    assert len(result) == 1
    assert result[0].result.total_cost == Decimal(0)


def test_context_aware_store_error_propagates(tmp_path):
    class _FailingStore:
        def query(self, *args, **kwargs):
            raise RuntimeError("store unavailable")

        def close(self):
            pass

    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)
    with pytest.raises(RuntimeError, match="store unavailable"):
        _context_rolling(_FailingStore(), windows, policy_factory=_flat_policy)


# =====================================================================
# Overlap / order / independence
# =====================================================================


def test_context_aware_output_order_equals_input_order(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W2.start, evaluation=W3),
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert tuple(r.window for r in result) == (W3, W1)


def test_context_aware_window_result_window_equals_evaluation_window(tmp_path):
    store = _candle_store(tmp_path)
    cw = ContextAwareWindow(context_start=W0.start, evaluation=W1)
    result = _context_rolling(store, (cw,), policy_factory=_flat_policy)
    assert result[0].window == cw.evaluation
    assert result[0].window is cw.evaluation


def test_context_aware_different_context_starts_for_different_windows(tmp_path):
    counting_store = _CountingCandleStore(_candle_store(tmp_path))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W2),  # long context
        ContextAwareWindow(context_start=W2.start, evaluation=W3),  # zero context
    )
    _context_rolling(counting_store, windows, policy_factory=_flat_policy)
    assert _query_boundaries(counting_store) == {(W0.start, W2.end), (W2.start, W3.end)}


def test_context_aware_duplicate_evaluation_windows_execute_independently(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2
    assert result[0].window == result[1].window == W1


def test_context_aware_overlapping_evaluation_windows_are_accepted(tmp_path):
    store = _candle_store(tmp_path)
    overlapping = TemporalWindow(
        start=datetime(2024, 1, 1, 11, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 13, 0, tzinfo=UTC)
    )
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=overlapping),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2


def test_context_aware_touching_evaluation_windows_are_accepted(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2


def test_context_aware_overlapping_context_intervals_are_accepted(tmp_path):
    store = _candle_store(tmp_path)
    # both windows' context ranges overlap ([08:00,10:00) and [08:00,12:00))
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W0.start, evaluation=W2),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2


def test_context_aware_context_overlapping_another_windows_evaluation_is_accepted(tmp_path):
    store = _candle_store(tmp_path)
    # window 1's context range [10:00,12:00) equals window 0's own evaluation
    # range W1 — legal, unrestricted.
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    assert len(result) == 2
    assert result[0].window == W1
    assert result[1].window == W2


def test_context_aware_no_state_passes_between_windows(tmp_path):
    class _CountingPolicy:
        def __init__(self):
            self.calls = 0

        def target_position(self, context):
            self.calls += 1
            return PositionTarget.FLAT

    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    created = []

    def factory():
        policy = _CountingPolicy()
        created.append(policy)
        return policy

    _context_rolling(store, windows, policy_factory=factory)
    assert [p.calls for p in created] == [2, 2]


def test_context_aware_repeated_equivalent_call_produces_equivalent_result(tmp_path):
    store = _candle_store(tmp_path)
    windows = (ContextAwareWindow(context_start=W0.start, evaluation=W1),)

    class _FixedPolicy:
        def target_position(self, context):
            return PositionTarget.FLAT

    first = _context_rolling(store, windows, policy_factory=_FixedPolicy)
    second = _context_rolling(store, windows, policy_factory=_FixedPolicy)
    assert first == second


def test_context_aware_config_is_not_mutated(tmp_path):
    store = _candle_store(tmp_path)
    config = _config()
    before = BacktestConfig(
        initial_cash=config.initial_cash, position_quantity=config.position_quantity
    )
    run_context_aware_rolling_backtest_from_store(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
        exchange=EXCHANGE,
        market_type=MARKET_TYPE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of_time=AS_OF_TIME,
        config=config,
        cost_model=ZeroCostModel(),
    )
    assert config == before


# =====================================================================
# Compatibility and metrics integration
# =====================================================================


def test_zero_context_runner_signature_unchanged():
    import inspect

    params = list(inspect.signature(run_rolling_backtest_from_store).parameters)
    assert params == [
        "store",
        "windows",
        "policy_factory",
        "exchange",
        "market_type",
        "symbol",
        "timeframe",
        "as_of_time",
        "config",
        "cost_model",
        "funding_required",
        "funding_store",
        "funding_model",
    ]


def test_context_aware_runner_not_exported_from_package_root_via_star_check():
    import crypto_quant_lab.validation as validation_package

    assert "run_context_aware_rolling_backtest_from_store" not in dir(validation_package)


def test_window_result_gains_no_fields_for_context_aware_support():
    from dataclasses import fields

    field_names = {f.name for f in fields(WindowResult)}
    assert field_names == {"window", "result"}


def test_temporal_window_and_split_unchanged_by_context_aware_addition():
    from dataclasses import fields

    assert {f.name for f in fields(TemporalWindow)} == {"start", "end"}
    assert {f.name for f in fields(TemporalSplit)} == {"in_sample", "out_of_sample", "timeframe"}


def test_context_aware_window_result_works_independently_with_stage1_metrics(tmp_path):
    store = _candle_store(tmp_path)
    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
    )
    metrics = compute_stage1_metrics(result[0].result)
    assert metrics.total_return == Decimal(0)
    assert metrics.max_drawdown == Decimal(0)


def test_context_aware_window_result_works_independently_with_periodic_returns(tmp_path):
    store = _candle_store(tmp_path)
    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
    )
    returns = compute_periodic_returns(result[0].result)
    # W1 has exactly 2 evaluation candles -> exactly 2 returns, never 4
    # (proving context candles are not diluting the return series).
    assert len(returns) == 2
    assert all(r == Decimal(0) for r in returns)


def test_context_aware_window_result_works_independently_with_stage2_metrics(tmp_path):
    prices = [Decimal(100), Decimal(110), Decimal(100), Decimal(120)]
    store = SQLiteHistoricalCandleStore(tmp_path / "moving.db")
    store.write_batch(
        [
            _make_record(datetime(2024, 1, 1, 8 + i, 0, tzinfo=UTC), price=price)
            for i, price in enumerate(prices)
        ]
    )
    long_window = TemporalWindow(
        start=datetime(2024, 1, 1, 9, 0, tzinfo=UTC), end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    )
    windows = (
        ContextAwareWindow(
            context_start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC), evaluation=long_window
        ),
    )
    result = _context_rolling(
        store,
        windows,
        policy_factory=lambda: _RecordingPolicy(lambda context: PositionTarget.LONG),
    )
    metrics = compute_stage2_metrics(result[0].result)
    assert metrics.return_stdev >= Decimal(0)


def test_context_aware_metrics_receive_evaluation_only_curve_without_second_filter(tmp_path):
    store = _candle_store(tmp_path)
    result = _context_rolling(
        store,
        (ContextAwareWindow(context_start=W0.start, evaluation=W1),),
        policy_factory=_flat_policy,
    )
    # compute_periodic_returns accepts the raw BacktestResult directly, with
    # no evaluation_start/context_start argument anywhere in its signature.
    import inspect

    assert "evaluation_start" not in inspect.signature(compute_periodic_returns).parameters
    assert len(compute_periodic_returns(result[0].result)) == len(result[0].result.equity_curve)


def test_no_cross_window_metric_aggregation_for_context_aware_results(tmp_path):
    store = _candle_store(tmp_path)
    windows = (
        ContextAwareWindow(context_start=W0.start, evaluation=W1),
        ContextAwareWindow(context_start=W1.start, evaluation=W2),
    )
    result = _context_rolling(store, windows, policy_factory=_flat_policy)
    per_window_metrics = tuple(compute_stage1_metrics(r.result) for r in result)
    assert len(per_window_metrics) == 2
    assert all(m.total_return == Decimal(0) for m in per_window_metrics)


def test_rolling_module_introduces_no_candidate_trial_or_optimizer_coupling():
    import crypto_quant_lab.validation.rolling as rolling_module

    assert not hasattr(rolling_module, "Candidate")
    assert not hasattr(rolling_module, "Trial")
    assert not hasattr(rolling_module, "optimize")
    assert not hasattr(rolling_module, "aggregate_candidates")
