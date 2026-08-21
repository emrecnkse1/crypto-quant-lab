import gc
import weakref
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
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
from crypto_quant_lab.validation.rolling import WindowResult, run_rolling_backtest_from_store
from crypto_quant_lab.validation.windows import TemporalSplit, TemporalWindow

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
